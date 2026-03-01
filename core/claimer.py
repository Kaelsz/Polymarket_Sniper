"""
Auto-claim resolved positions via Gnosis Safe execTransaction.

The conditional tokens sit in the proxy wallet (Gnosis Safe).
To redeem them for USDC.e, we build a redeemPositions call,
sign it as a Safe owner, and execute via the Safe contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger("polysniper.claimer")

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
ZERO_ADDR = "0x" + "0" * 40

RPCS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
    "https://polygon.meowrpc.com",
]

SAFE_ABI = json.loads("""[
    {"inputs":[],"name":"nonce","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"to","type":"address"},{"name":"value","type":"uint256"},{"name":"data","type":"bytes"},{"name":"operation","type":"uint8"},{"name":"safeTxGas","type":"uint256"},{"name":"baseGas","type":"uint256"},{"name":"gasPrice","type":"uint256"},{"name":"gasToken","type":"address"},{"name":"refundReceiver","type":"address"},{"name":"_nonce","type":"uint256"}],"name":"getTransactionHash","outputs":[{"internalType":"bytes32","name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"to","type":"address"},{"name":"value","type":"uint256"},{"name":"data","type":"bytes"},{"name":"operation","type":"uint8"},{"name":"safeTxGas","type":"uint256"},{"name":"baseGas","type":"uint256"},{"name":"gasPrice","type":"uint256"},{"name":"gasToken","type":"address"},{"name":"refundReceiver","type":"address payable"},{"name":"signatures","type":"bytes"}],"name":"execTransaction","outputs":[{"internalType":"bool","name":"success","type":"bool"}],"stateMutability":"payable","type":"function"}
]""")

CTF_REDEEM_ABI = json.loads("""[
    {"inputs":[{"name":"collateralToken","type":"address"},{"name":"parentCollectionId","type":"bytes32"},{"name":"conditionId","type":"bytes32"},{"name":"indexSets","type":"uint256[]"}],"name":"redeemPositions","outputs":[],"stateMutability":"nonpayable","type":"function"}
]""")


class PositionClaimer:
    """Claims resolved Polymarket positions by calling redeemPositions via Gnosis Safe."""

    def __init__(self, private_key: str, safe_address: str) -> None:
        self._pk = private_key
        self._safe_address_raw = safe_address
        self._w3: Any = None
        self._eoa: str | None = None

    def _lazy_init(self) -> None:
        from eth_account import Account
        from web3 import Web3

        self._safe_address = Web3.to_checksum_address(self._safe_address_raw)
        self._eoa = Account.from_key(self._pk).address

    def _connect(self) -> Any:
        from web3 import Web3

        if self._w3 is not None:
            try:
                self._w3.eth.chain_id
                return self._w3
            except Exception:
                self._w3 = None

        for rpc in RPCS:
            try:
                w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
                w3.eth.chain_id
                self._w3 = w3
                return w3
            except Exception:
                continue

        raise ConnectionError("No working Polygon RPC found")

    async def redeem(self, condition_id: str) -> dict[str, Any] | None:
        """
        Redeem a resolved position via Gnosis Safe execTransaction.
        Returns the transaction receipt, or None if redeem failed.
        """
        loop = asyncio.get_running_loop()
        try:
            receipt = await loop.run_in_executor(
                None, self._redeem_sync, condition_id
            )
            return receipt
        except Exception as exc:
            log.error("Redeem failed for condition %s: %s", condition_id[:16], exc)
            return None

    def _redeem_sync(self, condition_id: str) -> dict[str, Any]:
        from eth_account import Account
        from web3 import Web3

        if self._eoa is None:
            self._lazy_init()

        w3 = self._connect()

        safe_addr = Web3.to_checksum_address(self._safe_address_raw)
        ctf_addr = Web3.to_checksum_address(CTF_ADDRESS)
        usdc_addr = Web3.to_checksum_address(USDC_E)
        zero_addr = Web3.to_checksum_address(ZERO_ADDR)

        eoa_balance = w3.eth.get_balance(self._eoa)
        if eoa_balance < w3.to_wei(0.005, "ether"):
            raise RuntimeError(
                f"EOA {self._eoa} has insufficient POL for gas "
                f"({w3.from_wei(eoa_balance, 'ether'):.4f} POL). "
                f"Send at least 0.01 POL to the EOA."
            )

        ctf = w3.eth.contract(address=ctf_addr, abi=CTF_REDEEM_ABI)
        safe = w3.eth.contract(address=safe_addr, abi=SAFE_ABI)

        if condition_id.startswith("0x"):
            cond_bytes = bytes.fromhex(condition_id[2:])
        else:
            cond_bytes = bytes.fromhex(condition_id)

        redeem_data = ctf.encode_abi(
            fn_name="redeemPositions",
            args=[
                usdc_addr,
                b"\x00" * 32,
                cond_bytes,
                [1, 2],
            ],
        )

        safe_nonce = safe.functions.nonce().call()

        tx_hash = safe.functions.getTransactionHash(
            ctf_addr,
            0,
            bytes.fromhex(redeem_data[2:]),
            0,  # Call operation
            0,  # safeTxGas
            0,  # baseGas
            0,  # gasPrice
            zero_addr,  # gasToken
            zero_addr,  # refundReceiver
            safe_nonce,
        ).call()

        account = Account.from_key(self._pk)
        sig = account.unsafe_sign_hash(tx_hash)

        signature = (
            sig.r.to_bytes(32, "big")
            + sig.s.to_bytes(32, "big")
            + bytes([sig.v])
        )

        exec_tx = safe.functions.execTransaction(
            ctf_addr,
            0,
            bytes.fromhex(redeem_data[2:]),
            0,
            0,
            0,
            0,
            zero_addr,
            zero_addr,
            signature,
        ).build_transaction({
            "from": self._eoa,
            "nonce": w3.eth.get_transaction_count(self._eoa),
            "gasPrice": w3.eth.gas_price,
            "chainId": 137,
        })
        exec_tx["gas"] = w3.eth.estimate_gas(exec_tx)

        signed_tx = w3.eth.account.sign_transaction(exec_tx, self._pk)
        raw_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(raw_hash, timeout=120)

        status = receipt.get("status", 0)
        gas_used = receipt.get("gasUsed", 0)
        tx_hex = raw_hash.hex()
        log.info(
            "REDEEM %s | status=%s gas=%d tx=%s",
            condition_id[:16], "OK" if status == 1 else "FAILED",
            gas_used, tx_hex[:20],
        )

        if status != 1:
            raise RuntimeError(f"Redeem tx reverted: {tx_hex}")

        return dict(receipt)

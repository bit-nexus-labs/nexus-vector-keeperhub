from __future__ import annotations

import unittest

from nexus_vector.domain.verification_evidence import VerificationObservationStatus
from nexus_vector.integrations.base_sepolia_rpc_verification import (
    BASE_SEPOLIA_CHAIN_ID,
    ERC20_TRANSFER_TOPIC,
    BaseSepoliaErc20TransferVerifier,
    BaseSepoliaVerificationError,
)

TX_HASH = "0x" + "11" * 32
BLOCK_HASH = "0x" + "22" * 32
TOKEN = "0x" + "33" * 20
SENDER = "0x" + "44" * 20
RECIPIENT = "0x" + "55" * 20
OTHER = "0x" + "66" * 20


def topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def uint256(value: int) -> str:
    return "0x" + f"{value:064x}"


def transfer_log(
    *,
    token: str = TOKEN,
    sender: str = SENDER,
    recipient: str = RECIPIENT,
    amount: int = 7,
    log_index: int = 0,
):
    return {
        "address": token,
        "topics": [
            ERC20_TRANSFER_TOPIC,
            topic_address(sender),
            topic_address(recipient),
        ],
        "data": uint256(amount),
        "transactionHash": TX_HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x64",
        "logIndex": hex(log_index),
    }


def receipt(*logs, status="0x1"):
    return {
        "transactionHash": TX_HASH,
        "status": status,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x64",
        "logs": list(logs),
    }


class FakeRpc:
    def __init__(self, *, chain_id=BASE_SEPOLIA_CHAIN_ID, tx_receipt=None, latest=102):
        self.chain_id = chain_id
        self.tx_receipt = tx_receipt
        self.latest = latest
        self.calls = 0
        self.methods = []

    def call(self, method, params):
        self.calls += 1
        self.methods.append((method, list(params)))
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_getTransactionReceipt":
            return self.tx_receipt
        if method == "eth_blockNumber":
            return hex(self.latest)
        raise AssertionError(method)


def verifier(rpc):
    return BaseSepoliaErc20TransferVerifier(
        rpc,
        transaction_hash=TX_HASH,
        expected_token_address=TOKEN,
        expected_sender=SENDER,
        expected_recipient=RECIPIENT,
        expected_amount_base_units=7,
    )


class BaseSepoliaRpcVerificationTests(unittest.TestCase):
    def test_exact_transfer_is_verified_with_confirmations(self):
        rpc = FakeRpc(tx_receipt=receipt(transfer_log()), latest=102)
        observation = verifier(rpc).observe(object())

        self.assertEqual(
            observation.status,
            VerificationObservationStatus.VERIFIED_TRANSFER,
        )
        self.assertIsNotNone(observation.transfer)
        self.assertEqual(observation.transfer.chain_id, 84532)
        self.assertEqual(observation.transfer.token_address, TOKEN)
        self.assertEqual(observation.transfer.sender, SENDER)
        self.assertEqual(observation.transfer.recipient, RECIPIENT)
        self.assertEqual(observation.transfer.amount_base_units, 7)
        self.assertEqual(observation.transfer.confirmations, 3)
        self.assertEqual(rpc.calls, 3)
        self.assertEqual(
            [method for method, _ in rpc.methods],
            ["eth_chainId", "eth_getTransactionReceipt", "eth_blockNumber"],
        )

    def test_missing_receipt_is_not_found_without_latest_block_call(self):
        rpc = FakeRpc(tx_receipt=None)
        observation = verifier(rpc).observe(object())

        self.assertEqual(observation.status, VerificationObservationStatus.NOT_FOUND)
        self.assertEqual(rpc.calls, 2)

    def test_single_wrong_token_transfer_is_returned_for_reconciliation_to_block(self):
        wrong_token = "0x" + "77" * 20
        rpc = FakeRpc(tx_receipt=receipt(transfer_log(token=wrong_token)))
        observation = verifier(rpc).observe(object())

        self.assertEqual(
            observation.status,
            VerificationObservationStatus.VERIFIED_TRANSFER,
        )
        self.assertEqual(observation.transfer.token_address, wrong_token)

    def test_multiple_expected_token_transfers_choose_one_exact_match(self):
        rpc = FakeRpc(
            tx_receipt=receipt(
                transfer_log(recipient=OTHER, amount=8, log_index=0),
                transfer_log(log_index=1),
            )
        )
        observation = verifier(rpc).observe(object())

        self.assertEqual(
            observation.status,
            VerificationObservationStatus.VERIFIED_TRANSFER,
        )
        self.assertEqual(observation.transfer.log_index, 1)

    def test_duplicate_exact_transfers_are_ambiguous(self):
        rpc = FakeRpc(
            tx_receipt=receipt(
                transfer_log(log_index=0),
                transfer_log(log_index=1),
            )
        )
        observation = verifier(rpc).observe(object())

        self.assertEqual(observation.status, VerificationObservationStatus.AMBIGUOUS)
        self.assertIsNone(observation.transfer)

    def test_wrong_chain_fails_closed(self):
        rpc = FakeRpc(chain_id=8453, tx_receipt=receipt(transfer_log()))
        with self.assertRaises(BaseSepoliaVerificationError) as caught:
            verifier(rpc).observe(object())
        self.assertEqual(caught.exception.code, "WRONG_RPC_CHAIN")
        self.assertEqual(rpc.calls, 1)

    def test_reverted_receipt_fails_closed(self):
        rpc = FakeRpc(tx_receipt=receipt(transfer_log(), status="0x0"))
        with self.assertRaises(BaseSepoliaVerificationError) as caught:
            verifier(rpc).observe(object())
        self.assertEqual(caught.exception.code, "TRANSACTION_REVERTED")
        self.assertEqual(rpc.calls, 2)

    def test_malformed_expected_token_transfer_fails_closed(self):
        bad = transfer_log()
        bad["topics"] = [ERC20_TRANSFER_TOPIC, topic_address(SENDER)]
        rpc = FakeRpc(tx_receipt=receipt(bad))
        with self.assertRaises(BaseSepoliaVerificationError) as caught:
            verifier(rpc).observe(object())
        self.assertEqual(caught.exception.code, "INVALID_TRANSFER_TOPIC_COUNT")

    def test_receipt_log_binding_mismatch_fails_closed(self):
        bad = transfer_log()
        bad["transactionHash"] = "0x" + "99" * 32
        rpc = FakeRpc(tx_receipt=receipt(bad))
        with self.assertRaises(BaseSepoliaVerificationError) as caught:
            verifier(rpc).observe(object())
        self.assertEqual(caught.exception.code, "TRANSFER_RECEIPT_BINDING_MISMATCH")


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest

from device_registry import DeviceRegistry


class TestDeviceRegistry(unittest.TestCase):
    def test_pair_approve_revoke(self):
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "devices.db")
            reg = DeviceRegistry(db_path=db)
            req = reg.pair_request(device_name="laptop-a", requester_user_id="u1", metadata={"os": "win"})
            self.assertTrue(req.get("ok"))
            device_id = str(req.get("device_id"))
            request_id = str(req.get("request_id"))
            self.assertFalse(reg.is_trusted(device_id))

            approved = reg.approve(request_id=request_id, approver_user_id="owner", approval_note="ok")
            self.assertTrue(approved.get("ok"))
            self.assertTrue(reg.is_trusted(device_id))

            revoked = reg.revoke(device_id=device_id, revoked_by="owner", reason="test")
            self.assertTrue(revoked.get("ok"))
            self.assertFalse(reg.is_trusted(device_id))


if __name__ == "__main__":
    unittest.main()

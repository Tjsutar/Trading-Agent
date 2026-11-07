"""
Unit tests for SessionManager (BRD FR-EX-003).
"""
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from core.session_manager import SessionManager
from db.database import init_db
from sqlalchemy import create_engine


class TestSessionManager(unittest.TestCase):
    def setUp(self):
        # Configure in-memory database
        test_engine = create_engine("sqlite:///:memory:")
        init_db(test_engine)
        import db.database as db_module
        db_module.ScopedSession.remove()
        db_module.engine = test_engine
        db_module.SessionFactory.configure(bind=test_engine, expire_on_commit=False)
        db_module.ScopedSession.configure(bind=test_engine, expire_on_commit=False)

    def tearDown(self):
        import db.database as db_module
        db_module.ScopedSession.remove()

    def test_mock_session_connect(self):
        mock_api = MagicMock()
        mgr = SessionManager(is_mock=True, mock_instance=mock_api)
        connected = mgr.connect()
        self.assertTrue(connected)
        self.assertTrue(mgr.is_authenticated)
        self.assertEqual(mgr.get_api(), mock_api)

    def test_proactive_renewal_trigger(self):
        mock_api = MagicMock()
        mgr = SessionManager(is_mock=False, mock_instance=mock_api)
        mgr.is_authenticated = True
        # Set expiry to 5 minutes from now (within 10-minute proactive threshold)
        mgr.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=5)
        
        # Mock _perform_login
        mgr._perform_login = MagicMock(return_value=True)
        
        renewed = mgr.check_and_renew()
        self.assertTrue(renewed)
        mgr._perform_login.assert_called_once()

    def test_reconnect_retry_flow(self):
        mgr = SessionManager(is_mock=False)
        mgr._perform_login = MagicMock(side_effect=[False, False, True])
        
        # Should succeed on attempt 3
        result = mgr.reconnect(max_retries=3, initial_delay=0.01)
        self.assertTrue(result)
        self.assertEqual(mgr._perform_login.call_count, 3)


if __name__ == "__main__":
    unittest.main()

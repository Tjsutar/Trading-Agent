"""
Institutional Broker Session Manager (BRD FR-EX-003).
Handles proactive token renewal, auto-relogin with exponential backoff,
session health monitoring, and audit persistence.
"""
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Callable
import pyotp
from SmartApi import SmartConnect
from config.settings import Config
from db.repository import TradeRepository
from utils.logger import get_logger

logger = get_logger("SessionManager")


class SessionManager:
    """
    Manages active broker sessions, token lifetimes, and proactive renewals.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        password: Optional[str] = None,
        totp_secret: Optional[str] = None,
        is_mock: bool = False,
        mock_instance: Optional[Any] = None
    ):
        self.api_key = api_key or Config.API_KEY
        self.client_id = client_id or Config.CLIENT_ID
        self.password = password or Config.PASSWORD
        self.totp_secret = totp_secret or Config.TOTP_SECRET
        self.is_mock = is_mock
        self.mock_instance = mock_instance

        self.api: Optional[Any] = None
        self.session_data: Optional[Dict[str, Any]] = None
        self.feed_token: Optional[str] = None
        self.jwt_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        
        self.login_time: Optional[datetime] = None
        self.token_expiry: Optional[datetime] = None
        self.is_authenticated: bool = False
        self.consecutive_failures: int = 0
        
        self._lock = threading.RLock()
        self._renewal_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def connect(self) -> bool:
        """Initial connection to the broker."""
        with self._lock:
            if self.is_mock:
                self.api = self.mock_instance
                self.is_authenticated = True
                self.login_time = datetime.now(timezone.utc)
                self.token_expiry = self.login_time + timedelta(hours=24)
                logger.info("SessionManager: Connected using Mock Broker.")
                TradeRepository.record_audit(
                    event_type="SESSION_ESTABLISHED",
                    severity="INFO",
                    message="Mock session established successfully."
                )
                return True

            return self._perform_login()

    def _perform_login(self) -> bool:
        """Executes authentication flow with Angel One SmartAPI."""
        try:
            if not all([self.api_key, self.client_id, self.password, self.totp_secret]):
                logger.error("Missing Angel One credentials in environment.")
                return False

            self.api = SmartConnect(api_key=self.api_key)
            totp = pyotp.TOTP(self.totp_secret).now()
            
            data = self.api.generateSession(self.client_id, self.password, totp)
            
            if data and data.get("status"):
                self.session_data = data.get("data", {})
                self.jwt_token = self.session_data.get("jwtToken")
                self.refresh_token = self.session_data.get("refreshToken")
                self.feed_token = self.api.getfeedToken()
                
                self.login_time = datetime.now(timezone.utc)
                # Angel One tokens typically expire in 24 hours. We set proactive renewal at 23h 50m
                self.token_expiry = self.login_time + timedelta(hours=23, minutes=50)
                self.is_authenticated = True
                self.consecutive_failures = 0
                
                logger.info(f"SessionManager: Authentication successful for client {self.client_id}.")
                TradeRepository.record_audit(
                    event_type="SESSION_ESTABLISHED",
                    severity="INFO",
                    message=f"Session successfully established for client {self.client_id}",
                    context={"login_time": self.login_time.isoformat(), "client_id": self.client_id}
                )
                return True
            else:
                msg = data.get("message") if data else "Unknown response"
                logger.error(f"SessionManager: Login failed: {msg}")
                self.is_authenticated = False
                TradeRepository.record_audit(
                    event_type="SESSION_FAILED",
                    severity="CRITICAL",
                    message=f"Session login failed: {msg}"
                )
                return False
        except Exception as e:
            logger.error(f"SessionManager: Exception during login: {e}")
            self.is_authenticated = False
            TradeRepository.record_audit(
                event_type="SESSION_ERROR",
                severity="CRITICAL",
                message=f"Exception during session login: {str(e)}"
            )
            return False

    def reconnect(self, max_retries: int = 3, initial_delay: float = 2.0) -> bool:
        """
        Auto-relogin with exponential backoff on connection drop or invalid token (FR-EX-003).
        """
        with self._lock:
            delay = initial_delay
            for attempt in range(1, max_retries + 1):
                logger.warning(f"SessionManager: Reconnection attempt {attempt}/{max_retries}...")
                TradeRepository.record_audit(
                    event_type="SESSION_RECONNECT_ATTEMPT",
                    severity="WARNING",
                    message=f"Attempting session reconnect #{attempt}",
                    context={"attempt": attempt, "max_retries": max_retries}
                )
                
                if self.is_mock:
                    self.is_authenticated = True
                    return True
                
                if self._perform_login():
                    logger.info("SessionManager: Reconnection succeeded.")
                    return True
                
                self.consecutive_failures += 1
                time.sleep(delay)
                delay *= 2  # Exponential backoff

            logger.critical(f"SessionManager: All {max_retries} reconnection attempts failed! Alerting administrator.")
            TradeRepository.record_audit(
                event_type="SESSION_CRITICAL_FAILURE",
                severity="CRITICAL",
                message=f"All {max_retries} session reconnection attempts failed.",
                context={"consecutive_failures": self.consecutive_failures}
            )
            return False

    def check_and_renew(self) -> bool:
        """
        Proactively renews token if within 10 minutes of expiry (FR-EX-003).
        """
        with self._lock:
            if not self.is_authenticated or self.is_mock:
                return True

            now = datetime.now(timezone.utc)
            if self.token_expiry and (self.token_expiry - now) <= timedelta(minutes=10):
                logger.info("SessionManager: Token approaching expiry. Initiating proactive renewal...")
                TradeRepository.record_audit(
                    event_type="SESSION_PROACTIVE_RENEWAL",
                    severity="INFO",
                    message="Proactive token renewal triggered within 10-minute expiry window."
                )
                return self._perform_login()
            return True

    def get_api(self):
        """Returns the active broker API instance, checking session health first."""
        with self._lock:
            self.check_and_renew()
            return self.api

    def start_renewal_daemon(self, interval_seconds: int = 60):
        """Spawns background thread to monitor and renew session proactively."""
        def _daemon_loop():
            while not self._stop_event.is_set():
                try:
                    self.check_and_renew()
                except Exception as err:
                    logger.error(f"SessionManager Daemon Error: {err}")
                self._stop_event.wait(interval_seconds)

        self._renewal_thread = threading.Thread(target=_daemon_loop, daemon=True, name="SessionRenewerThread")
        self._renewal_thread.start()
        logger.info(f"SessionManager: Background renewal daemon started (interval: {interval_seconds}s).")

    def stop_renewal_daemon(self):
        """Stops the background renewal daemon."""
        if self._renewal_thread and self._renewal_thread.is_alive():
            self._stop_event.set()
            self._renewal_thread.join(timeout=2.0)
            logger.info("SessionManager: Background renewal daemon stopped.")

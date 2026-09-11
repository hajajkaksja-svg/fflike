# config.py
import os
from datetime import timedelta

class Config:
    # ---------- Security ----------
    SECRET_KEY = os.environ.get("SECRET_KEY", "ffboosthub-royal-secret-please-change")
    
    # ---------- Database ----------
    DB_FILE = "database.db"
    
    # ---------- Admin ----------
    ADMIN_USER = "admin"
    ADMIN_PASS = "1234"
    
    # ---------- Currency (INR) ----------
    CURRENCY_SYMBOL = "₹"
    CURRENCY_CODE = "INR"
    
    # ---------- Wallet ----------
    REFERRAL_BONUS = 50.00      # ₹50 per referral
    MIN_TRANSFER = 20.00        # ₹20 min transfer
    MIN_DEPOSIT = 50.00         # ₹50 min deposit
    
    # ---------- Session ----------
    SESSION_LIFETIME = timedelta(hours=2)
    MAX_LOGIN_ATTEMPTS = 5
    LOGIN_LOCKOUT = 300         # seconds
    
    # ---------- API ----------
    API_TIMEOUT = 30
    DEFAULT_SERVERS = "IND,BR,SG,EU,US,RU,ID,ME,AF,NA,SA,OC,JP,KR,CN,TR"
    BAN_API_URL = "http://13.48.49.51:5001/Bmw"
    DEFAULT_LIKE_API_URL = "https://likebot2029-three.vercel.app/like"
    
    # ---------- FamGateway ----------
    FAMGATEWAY_API_KEY = os.environ.get("FAMGATEWAY_API_KEY", "fam_2cb8be61cc9ee58101b34420277ff79c60b1c4bc")
    FAMGATEWAY_CREATE_URL = "https://famgateway.in/api/create-order"
    FAMGATEWAY_VERIFY_URL = "https://famgateway.in/api/verify-order.php"
    
    # ---------- Instant Like Defaults ----------
    DEFAULT_INSTANT_LIKE_COST = 30.00
    DEFAULT_INSTANT_LIKE_COUNT = 1000
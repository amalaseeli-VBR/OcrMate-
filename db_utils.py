import yaml
from pathlib import Path

try:
    import pyodbc
except Exception:
    pyodbc = None


class DatabaseConnector:
    def __init__(self, config_filename="customer_db_cred.yaml"):
        self.base_dir = Path(__file__).resolve().parent
        self.config_filename = config_filename
        self.cfg = self._load_config()

    def _load_config(self):
        defaults = {
            "server": None,
            "port": None,
            "database": None,
            "username": None,
            "password": None,
            "driver": "ODBC Driver 18 for SQL Server",
            "encrypt": "yes",
            "trust_server_certificate": "yes",
            "trusted_connection": None,
        }
        cfg = defaults.copy()
        sql_path = self.base_dir / self.config_filename
        if sql_path.exists():
            try:
                with open(sql_path, "r", encoding="utf-8") as file:
                    loaded_cfg = yaml.safe_load(file)
                if isinstance(loaded_cfg, dict):
                    cfg.update({k: v for k, v in loaded_cfg.items() if v is not None})
                    return cfg
                print(f"db_utils: Expected mapping in {sql_path}")
            except yaml.YAMLError as exc:
                print(f"db_utils: Error parsing {sql_path}: {exc}")
        legacy_cfg = self._read_legacy_yaml()
        if legacy_cfg:
            cfg.update(legacy_cfg)
        return cfg

    

    def _select_driver(self, cfg):
        if cfg.get("driver"):
            return cfg["driver"]
        if pyodbc is None:
            return None
        try:
            drivers = pyodbc.drivers()
            sql_drivers = [driver for driver in drivers if "SQL Server" in driver]
            if sql_drivers:
                return sql_drivers[-1]
            return drivers[-1] if drivers else None
        except Exception as exc:
            print(f"db_utils: Unable to probe installed ODBC drivers: {exc}")
            return None

    def _build_connection_string(self, cfg, driver):
        server = cfg.get("server")
        port = cfg.get("port")
        server_entry = f"{server},{port}" if port else server

        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server_entry}",
            f"DATABASE={cfg.get('database')}",
        ]

        trusted = cfg.get("trusted_connection")
        if trusted:
            parts.append(f"Trusted_Connection={trusted}")
        else:
            parts.append(f"UID={cfg.get('username')}")
            parts.append(f"PWD={cfg.get('password')}")

        encrypt = cfg.get("encrypt")
        if encrypt:
            parts.append(f"Encrypt={encrypt}")
        trust_cert = cfg.get("trust_server_certificate")
        if trust_cert:
            parts.append(f"TrustServerCertificate={trust_cert}")
        parts.append("Connection Timeout=5")
        return ";".join(parts) + ";"

    def create_connection(self):
        if pyodbc is None:
            print("pyodbc is not available. Please ensure the driver is installed.")
            return None

        self.cfg = self._load_config()
        cfg = self.cfg

        missing = []
        for key in ("server", "database"):
            if not cfg.get(key):
                missing.append(key)
        if not cfg.get("trusted_connection"):
            for key in ("username", "password"):
                if not cfg.get(key):
                    missing.append(key)

        if missing:
            print(f"Database configuration missing values: {', '.join(sorted(set(missing)))}")
            return None

        driver = self._select_driver(cfg)
        if not driver:
            print("No suitable ODBC driver found. Update customer_db_cred.yaml with a driver name or install one.")
            return None

        try:
            conn_str = self._build_connection_string(cfg, driver)
            conn = pyodbc.connect(conn_str)
            print("connection established")
            return conn
        except pyodbc.Error as exc:
            print(f"Database connection error: {exc}")
            print(
                "Please verify server, database, user, password, and ODBC driver.\n"
                f"Attempted server='{cfg.get('server')}', database='{cfg.get('database')}', "
                f"user='{cfg.get('username')}', driver='{driver}'"
            )
            return None
        
if __name__ == "__main__":
    db = DatabaseConnector()
    db.create_connection()
    
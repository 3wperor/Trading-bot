"""Configuration loading: secrets from .env / environment, parameters from config.yaml."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (without overriding)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


@dataclass
class StrategyConfig:
    name: str
    enabled: bool = True
    params: dict = field(default_factory=dict)


@dataclass
class Settings:
    # Secrets / connection
    oanda_token: str = ""
    oanda_account_id: str = ""
    oanda_env: str = "practice"  # "practice" or "live"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # Trading
    pairs: list = field(default_factory=lambda: ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD"])
    granularity: str = "M15"
    poll_interval: int = 30
    candle_count: int = 250
    combine_mode: str = "any"  # "any" (one fires, other not opposed) or "agree" (both must agree)
    exit_on_opposite: bool = True

    # Risk
    risk_per_trade_pct: float = 0.5
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 3.0
    max_open_trades: int = 3
    max_daily_loss_pct: float = 3.0
    max_spread_pips: float = 3.0
    max_units: int = 1_000_000

    # Strategies
    strategies: list = field(default_factory=list)

    # Storage / logging
    db_path: str = "trades.db"
    log_level: str = "INFO"

    @property
    def oanda_base_url(self) -> str:
        if self.oanda_env == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"


def load_settings(base_dir: Path | None = None) -> Settings:
    base_dir = base_dir or Path.cwd()
    load_env_file(base_dir / ".env")

    s = Settings()
    s.oanda_token = os.environ.get("OANDA_TOKEN", "")
    s.oanda_account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
    s.oanda_env = os.environ.get("OANDA_ENV", "practice").lower()
    s.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    s.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    cfg_path = base_dir / "config.yaml"
    raw = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text()) or {}

    trading = raw.get("trading", {})
    s.pairs = trading.get("pairs", s.pairs)
    s.granularity = trading.get("granularity", s.granularity)
    s.poll_interval = int(trading.get("poll_interval", s.poll_interval))
    s.candle_count = int(trading.get("candle_count", s.candle_count))
    s.combine_mode = trading.get("combine_mode", s.combine_mode)
    s.exit_on_opposite = bool(trading.get("exit_on_opposite", s.exit_on_opposite))

    risk = raw.get("risk", {})
    s.risk_per_trade_pct = float(risk.get("risk_per_trade_pct", s.risk_per_trade_pct))
    s.sl_atr_mult = float(risk.get("sl_atr_mult", s.sl_atr_mult))
    s.tp_atr_mult = float(risk.get("tp_atr_mult", s.tp_atr_mult))
    s.max_open_trades = int(risk.get("max_open_trades", s.max_open_trades))
    s.max_daily_loss_pct = float(risk.get("max_daily_loss_pct", s.max_daily_loss_pct))
    s.max_spread_pips = float(risk.get("max_spread_pips", s.max_spread_pips))
    s.max_units = int(risk.get("max_units", s.max_units))

    s.strategies = [
        StrategyConfig(name=item["name"], enabled=item.get("enabled", True),
                       params=item.get("params", {}))
        for item in raw.get("strategies", [])
    ] or [StrategyConfig("trend_following"), StrategyConfig("mean_reversion")]

    s.db_path = raw.get("db_path", s.db_path)
    s.log_level = raw.get("log_level", s.log_level)

    missing = [k for k, v in [("OANDA_TOKEN", s.oanda_token),
                              ("OANDA_ACCOUNT_ID", s.oanda_account_id),
                              ("TELEGRAM_BOT_TOKEN", s.telegram_token)] if not v]
    if missing:
        raise SystemExit(
            f"Missing required environment variables: {', '.join(missing)}.\n"
            "Copy .env.example to .env and fill in your credentials."
        )
    if s.oanda_env not in ("practice", "live"):
        raise SystemExit("OANDA_ENV must be 'practice' or 'live'")
    return s

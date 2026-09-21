import os
import sqlite3
import json
from contextlib import contextmanager
from typing import Dict, Any, List, Optional
import pandas as pd

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "forensics.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

class ForensicDatabase:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self, schema_file: str = SCHEMA_PATH):
        if not os.path.exists(schema_file):
            return
        with open(schema_file, "r", encoding="utf-8") as f:
            ddl = f.read()
        with self.get_connection() as conn:
            self._migrate_existing_tables(conn)
            conn.executescript(ddl)

    @staticmethod
    def _migrate_existing_tables(conn: sqlite3.Connection):
        """Add Phase 1 columns to existing databases before views are rebuilt."""
        migrations = {
            "tokens": {
                "chain_id": "INTEGER NOT NULL DEFAULT 4663",
                "is_qualified": "INTEGER DEFAULT 0",
                "is_training_anchor": "INTEGER DEFAULT 0",
                "qualification_reasons": "TEXT",
                "ath_source": "TEXT",
                "current_market_cap_usd": "REAL",
                "observed_peak_market_cap_usd": "REAL DEFAULT 0",
                "fdv_usd": "REAL",
                "current_liquidity_usd": "REAL",
                "market_pair_url": "TEXT",
                "market_data_at": "TEXT",
            },
            "execution_profiles": {
                "creation_block": "INTEGER", "creation_timestamp": "TEXT",
                "creation_gas_used": "INTEGER", "creation_tx_fee_eth": "REAL",
                "setup_time_seconds": "INTEGER", "wallet_age_at_deploy_seconds": "INTEGER",
                "funding_tx_hash": "TEXT", "first_funder": "TEXT", "first_funded_at": "TEXT",
                "funding_count_before_deploy": "INTEGER",
                "funding_total_eth_before_deploy": "REAL", "funding_lineage_json": "TEXT",
                "initial_snipe_tokens": "REAL", "initial_snipe_raw": "TEXT",
                "contract_factory": "TEXT", "deployer_balance_eth": "REAL",
            },
            "bytecode_profiles": {
                "normalized_bytecode_hash": "TEXT", "bytecode_size_bytes": "INTEGER",
                "metadata_hash": "TEXT", "metadata_length_bytes": "INTEGER",
                "selector_count": "INTEGER",
            },
        }
        existing_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        for table, columns in migrations.items():
            if table not in existing_tables:
                continue
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for column, declaration in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
                    if table == "tokens" and column == "is_training_anchor":
                        conn.execute(
                            "UPDATE tokens SET is_training_anchor=COALESCE(is_qualified, 0)"
                        )

    @staticmethod
    def _optional_float(value):
        if value is None or str(value).strip() == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_int(value):
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def upsert_token(self, data: Dict[str, Any]):
        sql = """
        INSERT INTO tokens (
            ca, chain_id, chain, symbol, name, launchpad, token_live_at, migrated_at,
            time_to_graduate_sec, is_migrated, is_dex_paid, is_qualified, is_training_anchor,
            qualification_reasons, ath_usd, ath_source, current_market_cap_usd,
            observed_peak_market_cap_usd, fdv_usd, current_liquidity_usd,
            market_pair_url, market_data_at, peak_liquidity_usd, x_handle,
            website, description
        ) VALUES (
            :ca, :chain_id, :chain, :symbol, :name, :launchpad, :token_live_at, :migrated_at,
            :time_to_graduate_sec, :is_migrated, :is_dex_paid, :is_qualified, :is_training_anchor,
            :qualification_reasons, :ath_usd, :ath_source, :current_market_cap_usd,
            :observed_peak_market_cap_usd, :fdv_usd, :current_liquidity_usd,
            :market_pair_url, :market_data_at, :peak_liquidity_usd, :x_handle,
            :website, :description
        )
        ON CONFLICT(ca) DO UPDATE SET
            chain_id = excluded.chain_id,
            chain = COALESCE(excluded.chain, tokens.chain),
            symbol = COALESCE(excluded.symbol, tokens.symbol),
            name = COALESCE(excluded.name, tokens.name),
            launchpad = COALESCE(excluded.launchpad, tokens.launchpad),
            token_live_at = COALESCE(excluded.token_live_at, tokens.token_live_at),
            migrated_at = COALESCE(excluded.migrated_at, tokens.migrated_at),
            time_to_graduate_sec = COALESCE(excluded.time_to_graduate_sec, tokens.time_to_graduate_sec),
            is_migrated = MAX(tokens.is_migrated, excluded.is_migrated),
            is_dex_paid = MAX(tokens.is_dex_paid, excluded.is_dex_paid),
            is_qualified = MAX(tokens.is_qualified, excluded.is_qualified),
            is_training_anchor = MAX(tokens.is_training_anchor, excluded.is_training_anchor),
            qualification_reasons = CASE
                WHEN tokens.is_training_anchor = 1 AND excluded.is_training_anchor = 0
                THEN tokens.qualification_reasons
                ELSE COALESCE(excluded.qualification_reasons, tokens.qualification_reasons)
            END,
            ath_usd = MAX(COALESCE(tokens.ath_usd, 0), COALESCE(excluded.ath_usd, 0)),
            ath_source = COALESCE(excluded.ath_source, tokens.ath_source),
            current_market_cap_usd = COALESCE(excluded.current_market_cap_usd, tokens.current_market_cap_usd),
            observed_peak_market_cap_usd = MAX(
                COALESCE(tokens.observed_peak_market_cap_usd, 0),
                COALESCE(excluded.observed_peak_market_cap_usd, 0)
            ),
            fdv_usd = COALESCE(excluded.fdv_usd, tokens.fdv_usd),
            current_liquidity_usd = COALESCE(excluded.current_liquidity_usd, tokens.current_liquidity_usd),
            market_pair_url = COALESCE(excluded.market_pair_url, tokens.market_pair_url),
            market_data_at = COALESCE(excluded.market_data_at, tokens.market_data_at),
            peak_liquidity_usd = MAX(COALESCE(tokens.peak_liquidity_usd, 0), COALESCE(excluded.peak_liquidity_usd, 0)),
            x_handle = COALESCE(excluded.x_handle, tokens.x_handle),
            website = COALESCE(excluded.website, tokens.website),
            description = COALESCE(excluded.description, tokens.description);
        """
        reasons = data.get("qualification_reasons")
        if reasons is not None and not isinstance(reasons, str):
            reasons = json.dumps(reasons, sort_keys=True)
        chain_id = int(data.get("chain_id") or 4663)
        default_chain = {4663: "RBH", 5042: "ARC", 8453: "BASE", 1: "ETH"}.get(
            chain_id, str(chain_id)
        )
        params = {
            "ca": str(data.get("ca") or "").lower(),
            "chain_id": chain_id,
            "chain": data.get("chain") or default_chain,
            "symbol": data.get("symbol"), "name": data.get("name"),
            "launchpad": data.get("launchpad"),
            "token_live_at": data.get("token_live_at") or data.get("token_live"),
            "migrated_at": data.get("migrated_at"),
            "time_to_graduate_sec": self._optional_int(data.get("time_to_graduate_sec")),
            "is_migrated": int(bool(data.get("is_migrated"))),
            "is_dex_paid": int(bool(data.get("is_dex_paid"))),
            "is_qualified": int(bool(data.get("is_qualified"))),
            "is_training_anchor": int(bool(data.get("is_training_anchor"))),
            "qualification_reasons": reasons,
            "ath_usd": self._optional_float(data.get("ath_usd", data.get("ath"))) or 0.0,
            "ath_source": data.get("ath_source"),
            "current_market_cap_usd": self._optional_float(data.get("current_market_cap_usd")),
            "observed_peak_market_cap_usd": self._optional_float(
                data.get("observed_peak_market_cap_usd", data.get("current_market_cap_usd"))
            ) or 0.0,
            "fdv_usd": self._optional_float(data.get("fdv_usd")),
            "current_liquidity_usd": self._optional_float(data.get("current_liquidity_usd")),
            "market_pair_url": data.get("market_pair_url"),
            "market_data_at": data.get("market_data_at"),
            "peak_liquidity_usd": self._optional_float(data.get("peak_liquidity_usd")) or 0.0,
            "x_handle": data.get("x_handle") or data.get("x"),
            "website": data.get("website"), "description": data.get("description"),
        }
        with self.get_connection() as conn:
            existing = conn.execute(
                "SELECT ca, chain_id FROM tokens WHERE LOWER(ca)=LOWER(?) ORDER BY created_at",
                (params["ca"],),
            ).fetchall()
            conflicting = {
                int(row["chain_id"] or 4663) for row in existing
                if int(row["chain_id"] or 4663) != chain_id
            }
            if conflicting:
                raise ValueError(
                    f"contract address {params['ca']} already belongs to chain(s) "
                    f"{sorted(conflicting)}; this database blocks cross-chain address collisions"
                )
            if existing:
                params["ca"] = existing[0]["ca"]
            conn.execute(sql, params)
        return params["ca"]

    def upsert_execution_profile(self, data: Dict[str, Any]):
        columns = (
            "ca", "dev_wallet", "funder_1hop", "funder_2hop", "funder_label",
            "fund_amount", "funded_at", "value_eth", "is_005_pattern", "gwei",
            "max_gwei", "priority_gwei", "nonce", "method_selector", "proxy_impl",
            "proxy_kind", "creation_tx_hash", "creation_block", "creation_timestamp",
            "creation_gas_used", "creation_tx_fee_eth", "setup_time_seconds",
            "wallet_age_at_deploy_seconds", "funding_tx_hash", "first_funder",
            "first_funded_at", "funding_count_before_deploy",
            "funding_total_eth_before_deploy", "funding_lineage_json",
            "initial_snipe_tokens", "initial_snipe_raw", "contract_factory",
            "deployer_balance_eth",
        )
        updates = ",\n            ".join(
            f"{column} = COALESCE(excluded.{column}, execution_profiles.{column})"
            for column in columns if column != "ca"
        )
        sql = f"""
        INSERT INTO execution_profiles ({', '.join(columns)})
        VALUES ({', '.join(':' + column for column in columns)})
        ON CONFLICT(ca) DO UPDATE SET {updates};
        """
        lineage = data.get("funding_lineage_json", data.get("funding_lineage"))
        if lineage is not None and not isinstance(lineage, str):
            lineage = json.dumps(lineage, sort_keys=True)
        value_eth = self._optional_float(data.get("value_eth", data.get("deployer_initial_capital")))
        params = {
            "ca": data.get("ca"),
            "dev_wallet": data.get("dev_wallet") or data.get("dev") or data.get("deployer_address"),
            "funder_1hop": data.get("funder_1hop") or data.get("funder") or data.get("funder_address"),
            "funder_2hop": data.get("funder_2hop") or data.get("funder_hop2_address"),
            "funder_label": data.get("funder_label") or data.get("funder_name"),
            "fund_amount": self._optional_float(data.get("fund_amount", data.get("dw_funded", data.get("eth_received")))),
            "funded_at": data.get("funded_at") or data.get("dw_date_funded") or data.get("funded_timestamp"),
            "value_eth": value_eth,
            "is_005_pattern": int(bool(value_eth and f"{value_eth:.9f}".rstrip("0").endswith("005"))),
            "gwei": self._optional_float(data.get("gwei", data.get("gas_price_gwei"))),
            "max_gwei": self._optional_float(data.get("max_gwei", data.get("max_fee_gwei"))),
            "priority_gwei": self._optional_float(data.get("priority_gwei", data.get("priority_fee_gwei"))),
            "nonce": self._optional_int(data.get("nonce", data.get("creation_nonce"))),
            "method_selector": data.get("method_selector") or data.get("method") or data.get("creation_method"),
            "proxy_impl": data.get("proxy_impl"), "proxy_kind": data.get("proxy_kind"),
            "creation_tx_hash": data.get("creation_tx_hash"),
            "creation_block": self._optional_int(data.get("creation_block")),
            "creation_timestamp": data.get("creation_timestamp"),
            "creation_gas_used": self._optional_int(data.get("creation_gas_used")),
            "creation_tx_fee_eth": self._optional_float(data.get("creation_tx_fee_eth", data.get("creation_tx_fee"))),
            "setup_time_seconds": self._optional_int(data.get("setup_time_seconds")),
            "wallet_age_at_deploy_seconds": self._optional_int(data.get("wallet_age_at_deploy_seconds")),
            "funding_tx_hash": data.get("funding_tx_hash"),
            "first_funder": data.get("first_funder") or data.get("first_funder_address"),
            "first_funded_at": data.get("first_funded_at") or data.get("first_funded_timestamp"),
            "funding_count_before_deploy": self._optional_int(data.get("funding_count_before_deploy")),
            "funding_total_eth_before_deploy": self._optional_float(data.get("funding_total_eth_before_deploy")),
            "funding_lineage_json": lineage,
            "initial_snipe_tokens": self._optional_float(data.get("initial_snipe_tokens", data.get("deployer_initial_snipe"))),
            "initial_snipe_raw": data.get("initial_snipe_raw") or data.get("deployer_initial_snipe_raw"),
            "contract_factory": data.get("contract_factory"),
            "deployer_balance_eth": self._optional_float(data.get("deployer_balance_eth")),
        }
        with self.get_connection() as conn:
            conn.execute(sql, params)

    def upsert_bytecode_profile(self, data: Dict[str, Any]):
        columns = (
            "ca", "template_hash", "raw_bytecode_hash", "compiler_version",
            "selectors_hash", "selectors_list", "normalized_bytecode_hash",
            "bytecode_size_bytes", "metadata_hash", "metadata_length_bytes",
            "selector_count",
        )
        updates = ",\n            ".join(
            f"{column} = COALESCE(excluded.{column}, bytecode_profiles.{column})"
            for column in columns if column != "ca"
        )
        sql = f"""
        INSERT INTO bytecode_profiles ({', '.join(columns)})
        VALUES ({', '.join(':' + column for column in columns)})
        ON CONFLICT(ca) DO UPDATE SET {updates};
        """
        selectors = data.get("selectors_list", data.get("function_selectors"))
        if isinstance(selectors, str):
            selector_values = [part for part in selectors.split(",") if part]
            selectors_json = json.dumps(selector_values)
        elif isinstance(selectors, list):
            selector_values = selectors
            selectors_json = json.dumps(selectors)
        else:
            selector_values, selectors_json = [], None
        params = {
            "ca": data.get("ca"),
            "template_hash": data.get("template_hash") or data.get("template") or data.get("normalized_bytecode_sha256"),
            "raw_bytecode_hash": data.get("raw_bytecode_hash") or data.get("bytecode_sha256"),
            "compiler_version": data.get("compiler_version"),
            "selectors_hash": data.get("selectors_hash") or data.get("method_ids_hash"),
            "selectors_list": selectors_json,
            "normalized_bytecode_hash": data.get("normalized_bytecode_hash") or data.get("normalized_bytecode_sha256"),
            "bytecode_size_bytes": self._optional_int(data.get("bytecode_size_bytes")),
            "metadata_hash": data.get("metadata_hash"),
            "metadata_length_bytes": self._optional_int(data.get("metadata_length_bytes")),
            "selector_count": self._optional_int(data.get("selector_count", data.get("function_selector_count"))) if not selector_values else len(selector_values),
        }
        with self.get_connection() as conn:
            conn.execute(sql, params)

    def upsert_bundle_analytics(self, data: Dict[str, Any]):
        sql = """
        INSERT INTO bundle_analytics (
            ca, bundler_wallet, bundle_eth, dev_eth, buyer_wallet, buyer_eth,
            bundle_ratio, bundle_wallets_count, dev_holding_ratio,
            time_social_paid, first_boost, hm_1st_boost, second_boost, hm_2nd_boost, ads_paid
        ) VALUES (
            :ca, :bundler_wallet, :bundle_eth, :dev_eth, :buyer_wallet, :buyer_eth,
            :bundle_ratio, :bundle_wallets_count, :dev_holding_ratio,
            :time_social_paid, :first_boost, :hm_1st_boost, :second_boost, :hm_2nd_boost, :ads_paid
        )
        ON CONFLICT(ca) DO UPDATE SET
            bundler_wallet = COALESCE(excluded.bundler_wallet, bundle_analytics.bundler_wallet),
            bundle_eth = COALESCE(excluded.bundle_eth, bundle_analytics.bundle_eth),
            dev_eth = COALESCE(excluded.dev_eth, bundle_analytics.dev_eth),
            buyer_wallet = COALESCE(excluded.buyer_wallet, bundle_analytics.buyer_wallet),
            buyer_eth = COALESCE(excluded.buyer_eth, bundle_analytics.buyer_eth),
            bundle_ratio = COALESCE(excluded.bundle_ratio, bundle_analytics.bundle_ratio),
            bundle_wallets_count = COALESCE(excluded.bundle_wallets_count, bundle_analytics.bundle_wallets_count),
            dev_holding_ratio = COALESCE(excluded.dev_holding_ratio, bundle_analytics.dev_holding_ratio),
            time_social_paid = COALESCE(excluded.time_social_paid, bundle_analytics.time_social_paid),
            first_boost = COALESCE(excluded.first_boost, bundle_analytics.first_boost),
            hm_1st_boost = COALESCE(excluded.hm_1st_boost, bundle_analytics.hm_1st_boost),
            second_boost = COALESCE(excluded.second_boost, bundle_analytics.second_boost),
            hm_2nd_boost = COALESCE(excluded.hm_2nd_boost, bundle_analytics.hm_2nd_boost),
            ads_paid = COALESCE(excluded.ads_paid, bundle_analytics.ads_paid);
        """
        params = {
            "ca": data.get("ca"),
            "bundler_wallet": data.get("bundler_wallet"),
            "bundle_eth": self._optional_float(data.get("bundle_eth")),
            "dev_eth": self._optional_float(data.get("dev_eth")),
            "buyer_wallet": data.get("buyer_wallet") or data.get("buyer"),
            "buyer_eth": self._optional_float(data.get("buyer_eth")),
            "bundle_ratio": self._optional_float(data.get("bundle_ratio")),
            "bundle_wallets_count": self._optional_int(data.get("bundle_wallets_count")),
            "dev_holding_ratio": self._optional_float(data.get("dev_holding_ratio")),
            "time_social_paid": data.get("time_social_paid"),
            "first_boost": data.get("first_boost") or data.get("1st_boost"),
            "hm_1st_boost": data.get("hm_1st_boost"),
            "second_boost": data.get("second_boost") or data.get("2nd_boost"),
            "hm_2nd_boost": data.get("hm_2nd_boost"),
            "ads_paid": data.get("ads_paid") or data.get("ads")
        }
        with self.get_connection() as conn:
            conn.execute(sql, params)

    def upsert_branding_profile(self, data: Dict[str, Any]):
        sql = """
        INSERT INTO branding_profiles (
            ca, website_url, website_domain, website_tld, website_host_type,
            favicon_hash, website_title_hash, tg_url, tg_handle,
            tg_naming_pattern, tg_chat_id_bracket, x_handle, x_naming_pattern, marketing_speed_sec
        ) VALUES (
            :ca, :website_url, :website_domain, :website_tld, :website_host_type,
            :favicon_hash, :website_title_hash, :tg_url, :tg_handle,
            :tg_naming_pattern, :tg_chat_id_bracket, :x_handle, :x_naming_pattern, :marketing_speed_sec
        )
        ON CONFLICT(ca) DO UPDATE SET
            website_url = COALESCE(excluded.website_url, branding_profiles.website_url),
            website_domain = COALESCE(excluded.website_domain, branding_profiles.website_domain),
            website_tld = COALESCE(excluded.website_tld, branding_profiles.website_tld),
            website_host_type = COALESCE(excluded.website_host_type, branding_profiles.website_host_type),
            favicon_hash = COALESCE(excluded.favicon_hash, branding_profiles.favicon_hash),
            website_title_hash = COALESCE(excluded.website_title_hash, branding_profiles.website_title_hash),
            tg_url = COALESCE(excluded.tg_url, branding_profiles.tg_url),
            tg_handle = COALESCE(excluded.tg_handle, branding_profiles.tg_handle),
            tg_naming_pattern = COALESCE(excluded.tg_naming_pattern, branding_profiles.tg_naming_pattern),
            tg_chat_id_bracket = COALESCE(excluded.tg_chat_id_bracket, branding_profiles.tg_chat_id_bracket),
            x_handle = COALESCE(excluded.x_handle, branding_profiles.x_handle),
            x_naming_pattern = COALESCE(excluded.x_naming_pattern, branding_profiles.x_naming_pattern),
            marketing_speed_sec = COALESCE(excluded.marketing_speed_sec, branding_profiles.marketing_speed_sec);
        """
        params = {
            "ca": data.get("ca"),
            "website_url": data.get("website_url"),
            "website_domain": data.get("website_domain"),
            "website_tld": data.get("website_tld"),
            "website_host_type": data.get("website_host_type", "NONE"),
            "favicon_hash": data.get("favicon_hash"),
            "website_title_hash": data.get("website_title_hash"),
            "tg_url": data.get("tg_url"),
            "tg_handle": data.get("tg_handle"),
            "tg_naming_pattern": data.get("tg_naming_pattern", "NONE"),
            "tg_chat_id_bracket": data.get("tg_chat_id_bracket"),
            "x_handle": data.get("x_handle"),
            "x_naming_pattern": data.get("x_naming_pattern", "NONE"),
            "marketing_speed_sec": data.get("marketing_speed_sec")
        }
        with self.get_connection() as conn:
            conn.execute(sql, params)

    def upsert_fingerprint_profile(self, data: Dict[str, Any]):
        columns = (
            "ca", "wallet_lineage_json", "funding_habits_json", "launch_habits_json",
            "contract_habits_json", "social_habits_json", "evidence_quality_json",
        )
        payload = {"ca": data.get("ca")}
        for column in columns[1:]:
            value = data.get(column)
            payload[column] = value if value is None or isinstance(value, str) else json.dumps(value, sort_keys=True)
        sql = f"""
        INSERT INTO fingerprint_profiles ({', '.join(columns)})
        VALUES ({', '.join(':' + column for column in columns)})
        ON CONFLICT(ca) DO UPDATE SET
            wallet_lineage_json=COALESCE(excluded.wallet_lineage_json, fingerprint_profiles.wallet_lineage_json),
            funding_habits_json=COALESCE(excluded.funding_habits_json, fingerprint_profiles.funding_habits_json),
            launch_habits_json=COALESCE(excluded.launch_habits_json, fingerprint_profiles.launch_habits_json),
            contract_habits_json=COALESCE(excluded.contract_habits_json, fingerprint_profiles.contract_habits_json),
            social_habits_json=COALESCE(excluded.social_habits_json, fingerprint_profiles.social_habits_json),
            evidence_quality_json=COALESCE(excluded.evidence_quality_json, fingerprint_profiles.evidence_quality_json),
            extracted_at=datetime('now');
        """
        with self.get_connection() as conn:
            conn.execute(sql, payload)

    def upsert_team(self, team_name: str, representative_template: Optional[str] = None, root_funder: Optional[str] = None, notes: Optional[str] = None) -> int:
        clean_name = team_name.strip()
        sql = """
        INSERT INTO teams (team_name, representative_template, root_funder, notes, first_seen_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(team_name) DO UPDATE SET
            representative_template = COALESCE(excluded.representative_template, teams.representative_template),
            root_funder = COALESCE(excluded.root_funder, teams.root_funder),
            notes = COALESCE(excluded.notes, teams.notes)
        RETURNING team_id;
        """
        with self.get_connection() as conn:
            cursor = conn.execute(sql, (clean_name, representative_template, root_funder, notes))
            row = cursor.fetchone()
            if row:
                return row["team_id"]
            cursor = conn.execute("SELECT team_id FROM teams WHERE team_name = ?", (clean_name,))
            return cursor.fetchone()["team_id"]

    def upsert_token_match(self, ca: str, best_match_ca: Optional[str], best_match_pct: float,
                           candidate_team_id: Optional[int], candidate_team_name: Optional[str],
                           match_reasons: Dict[str, Any]):
        sql = """
        INSERT INTO token_matches (ca, best_match_ca, best_match_pct, candidate_team_id, candidate_team_name, match_reasons)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(ca) DO UPDATE SET
            best_match_ca = excluded.best_match_ca,
            best_match_pct = excluded.best_match_pct,
            candidate_team_id = excluded.candidate_team_id,
            candidate_team_name = excluded.candidate_team_name,
            match_reasons = excluded.match_reasons,
            matched_at = datetime('now');
        """
        with self.get_connection() as conn:
            conn.execute(sql, (
                ca,
                best_match_ca,
                best_match_pct,
                candidate_team_id,
                candidate_team_name,
                json.dumps(match_reasons)
            ))

    def get_team_by_name(self, team_name: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM teams WHERE LOWER(team_name)=LOWER(?)", (team_name,)
            ).fetchone()
            return dict(row) if row else None

    def is_ground_truth_match(self, ca: str) -> bool:
        with self.get_connection() as conn:
            row = conn.execute("SELECT match_reasons FROM token_matches WHERE LOWER(ca)=LOWER(?)", (ca,)).fetchone()
        if not row or not row[0]:
            return False
        try:
            return bool(json.loads(row[0]).get("ground_truth"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False

    def get_token(self, ca: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM v_full_forensic_profile WHERE LOWER(ca) = LOWER(?);"
        with self.get_connection() as conn:
            row = conn.execute(sql, (ca,)).fetchone()
            return dict(row) if row else None

    def get_all_historical_tokens(self, exclude_ca: Optional[str] = None) -> List[Dict[str, Any]]:
        if exclude_ca:
            sql = "SELECT * FROM v_full_forensic_profile WHERE LOWER(ca) != LOWER(?) ORDER BY token_live_at DESC;"
            params = (exclude_ca,)
        else:
            sql = "SELECT * FROM v_full_forensic_profile ORDER BY token_live_at DESC;"
            params = ()
        with self.get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_team_tokens(self, team_name: str) -> List[Dict[str, Any]]:
        sql = """
        SELECT * FROM v_full_forensic_profile
        WHERE LOWER(candidate_team) = LOWER(?)
        ORDER BY token_live_at DESC;
        """
        with self.get_connection() as conn:
            rows = conn.execute(sql, (team_name,)).fetchall()
            return [dict(r) for r in rows]

    def get_team_summary(self) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM v_team_summary ORDER BY token_count DESC;"
        with self.get_connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def export_to_dataframe(self) -> pd.DataFrame:
        sql = "SELECT * FROM v_full_forensic_profile;"
        with self.get_connection() as conn:
            return pd.read_sql_query(sql, conn)

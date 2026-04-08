from base64 import b64encode
from pathlib import Path
from urllib import parse, request

from config_utils import load_config


POOL_ADDRESS = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
OUTPUT_PATH = Path(__file__).resolve().parent / "data_test.csv"
CLICKHOUSE_TABLE = "rrmt_logs"
ROW_LIMIT = 5000


def fetch_clickhouse_csv(query, clickhouse_config):
    url = f"http://{clickhouse_config['host']}:{clickhouse_config['port']}/?database={parse.quote(str(clickhouse_config['database']))}"
    auth = f"{clickhouse_config['user']}:{clickhouse_config['password']}"
    req = request.Request(url, data=query.encode("utf-8"), method="POST")
    req.add_header("Authorization", "Basic " + b64encode(auth.encode("utf-8")).decode("ascii"))
    req.add_header("Content-Type", "text/plain; charset=utf-8")

    with request.urlopen(req, timeout=30) as response:
        return response.read()


def build_query(pool_address, limit):
    safe_pool = pool_address.lower()
    return f"""
SELECT
    Hash,
    BlockNumber,
    Address,
    TopicHash,
    Topics,
    Data,
    CreatedAt,
    Serial,
    BlockHash
FROM {CLICKHOUSE_TABLE}
WHERE lower(Address) = '{safe_pool}'
ORDER BY BlockNumber ASC, Serial ASC
LIMIT {int(limit)}
FORMAT CSVWithNames
""".strip()


def main():
    config = load_config()
    clickhouse_config = config.get("clickhouse") or {}
    required_keys = ["host", "port", "user", "password", "database"]
    missing_keys = [key for key in required_keys if not clickhouse_config.get(key)]

    if missing_keys:
        missing = ", ".join(missing_keys)
        raise RuntimeError(f"config.yaml 缺少 clickhouse 配置项: {missing}")

    query = build_query(POOL_ADDRESS, ROW_LIMIT)
    csv_bytes = fetch_clickhouse_csv(query, clickhouse_config)
    OUTPUT_PATH.write_bytes(csv_bytes)
    row_count = max(len(csv_bytes.decode("utf-8").splitlines()) - 1, 0)
    print(f"已保存 {row_count} 条池日志到 {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

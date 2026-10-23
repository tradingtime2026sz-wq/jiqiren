"""从富途拉 VIXY（短期 VIX 期货 ETF，盘中 VIX 的替代）1 分钟线与日线。只读。"""
import time
from pathlib import Path

import pandas as pd
from futu import AuType, KLType, OpenQuoteContext, RET_OK

OUT = Path("data/research")


def pull(q, code, kt, start, end):
    rows, page = [], None
    while True:
        time.sleep(0.6)
        ret, df, page = q.request_history_kline(code, start=start, end=end, ktype=kt, autype=AuType.QFQ,
                                                max_count=1000, page_req_key=page)
        if ret != RET_OK:
            raise RuntimeError(df)
        rows.append(df[["time_key", "open", "high", "low", "close", "volume"]])
        if page is None:
            break
    return pd.concat(rows)


if __name__ == "__main__":
    q = OpenQuoteContext(host="127.0.0.1", port=11111)
    try:
        d = pull(q, "US.VIXY", KLType.K_DAY, "2024-06-01", "2026-10-22")
        d.to_csv(OUT / "vixy_1d.csv", index=False)
        m = pull(q, "US.VIXY", KLType.K_1M, "2024-10-01", "2026-10-22")
        m.to_csv(OUT / "vixy_1m.csv", index=False)
        print("daily", len(d), "minute", len(m), m["time_key"].iloc[0], "→", m["time_key"].iloc[-1])
    finally:
        q.close()

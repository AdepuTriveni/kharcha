# Kharcha parser dataset card

- Version: `623bb2f162a4`
- Built: 2026-10-01 14:56 UTC
- Splits by template (one template, one split); `gold_test` is human-verified and is
  **never used for training**; held-out banks appear only in `gold_test`.
- Near-duplicates removed from train/val: 5
- Real data only from users with `ml_consent = true`; redacted on the phone and again
  at export.

| Split | Examples | Templates | Reviewed |
|---|---|---|---|
| train | 4597 | 39 | 4597 |
| val | 450 | 4 | 450 |
| gold_test | 966 | 9 | 966 |

### Source

| | train | val | gold_test |
|---|---|---|---|
| HARD_NEGATIVE | 793 | 263 | 135 |
| SYNTHETIC | 3804 | 187 | 831 |

### Bank / app

| | train | val | gold_test |
|---|---|---|---|
| AXISBK | 307 | 0 | 161 |
| BOBTXN | 186 | 0 | 0 |
| CANBNK | 0 | 0 | 162 |
| HDFCBK | 661 | 130 | 166 |
| ICICIT | 452 | 0 | 0 |
| IDFCFB | 169 | 0 | 0 |
| INDUSB | 192 | 0 | 0 |
| KOTAKB | 524 | 0 | 1 |
| PNBSMS | 162 | 0 | 0 |
| SBIINB | 188 | 133 | 1 |
| SBIOTP | 129 | 0 | 0 |
| SBIUPI | 181 | 0 | 0 |
| UBOI | 0 | 0 | 181 |
| YESBNK | 150 | 1 | 0 |
| com.dreamplug.androidapp | 0 | 186 | 0 |
| com.google.android.apps.nbu.paisa.user | 295 | 0 | 161 |
| com.phonepe.app | 355 | 0 | 133 |
| in.amazon.mShop.android.shopping | 160 | 0 | 0 |
| in.org.npci.upiapp | 175 | 0 | 0 |
| net.one97.paytm | 311 | 0 | 0 |

### Status

| | train | val | gold_test |
|---|---|---|---|
| FAILED | 371 | 0 | 0 |
| NOT_TRANSACTION | 793 | 263 | 135 |
| PENDING | 150 | 1 | 0 |
| REVERSED | 193 | 0 | 0 |
| SUCCESS | 3090 | 186 | 831 |

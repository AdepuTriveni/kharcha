"""Synthetic bank/UPI messages with exact labels (PROJECT_SPEC §15.2 source 2 and 3).

Templates are written by hand from public examples of message *formats* (never copied from
user data). Each template knows where every field goes, so the label is exact: the amount is
copied as written, the reference and account hint are the generated ones. Formatting noise
(spacing, line breaks, truncation, Hindi words, emoji) is added on top. Hard negatives cover
OTPs, promos, "cashback up to", balance-only, collect requests and statements.

Deterministic for a given seed.
"""

import random
from collections.abc import Iterator
from dataclasses import dataclass, field

from kharcha_ml.dataset.labels import Extraction, LabeledExample


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    sender: str | None  # SMS header, e.g. "AX-HDFCBK"
    app: str | None  # notification package
    text: str  # str.format fields: amount acct vpa merchant ref date bal days person
    direction: str
    channel: str
    status: str = "SUCCESS"
    fields: frozenset[str] = field(default_factory=frozenset)  # label fields to fill
    merchant_from: str | None = None  # "vpa" | "merchant" | "person"


def T(  # noqa: N802 - table-like constructor
    id: str,
    sender: str | None,
    app: str | None,
    text: str,
    direction: str,
    channel: str,
    status: str = "SUCCESS",
    merchant_from: str | None = None,
) -> Template:
    names = {"amount", "acct", "vpa", "ref", "bal", "days", "merchant", "person"}
    used = frozenset(n for n in names if "{" + n + "}" in text)
    return Template(id, sender, app, text, direction, channel, status, used, merchant_from)


PHONEPE, GPAY, PAYTM = (
    "com.phonepe.app",
    "com.google.android.apps.nbu.paisa.user",
    "net.one97.paytm",
)
AMAZON, CRED, BHIM = (
    "in.amazon.mShop.android.shopping",
    "com.dreamplug.androidapp",
    "in.org.npci.upiapp",
)

TEMPLATES: tuple[Template, ...] = (
    T(
        "hdfc_upi_debit",
        "HDFCBK",
        None,
        "Rs.{amount} debited from A/c XX{acct} on {date} to VPA {vpa}. UPI Ref {ref}. "
        "Not you? Call 18002586161",
        "DEBIT",
        "UPI",
        merchant_from="vpa",
    ),
    T(
        "hdfc_upi_credit",
        "HDFCBK",
        None,
        "Rs.{amount} credited to A/c XX{acct} on {date} from VPA {vpa} (UPI Ref No {ref})",
        "CREDIT",
        "UPI",
        merchant_from="vpa",
    ),
    T(
        "hdfc_card",
        "HDFCBK",
        None,
        "Spent Rs.{amount} on HDFC Bank Card x{acct} at {merchant} on {date}. Avl Lmt Rs.{bal}",
        "DEBIT",
        "CARD",
        merchant_from="merchant",
    ),
    T(
        "hdfc_atm",
        "HDFCBK",
        None,
        "Rs {amount} withdrawn at ATM S1AN000{days} from A/c XX{acct} on {date}. Avl bal Rs {bal}",
        "DEBIT",
        "ATM",
    ),
    T(
        "sbi_upi_debit",
        "SBIUPI",
        None,
        "Dear UPI user A/C X{acct} debited by {amount} on date {date} trf to {merchant} "
        "Refno {ref}. If not u? call 1800111109. -SBI",
        "DEBIT",
        "UPI",
        merchant_from="merchant",
    ),
    T(
        "sbi_neft_credit",
        "SBIINB",
        None,
        "Dear Customer, your A/c XX{acct} is credited with Rs {amount} on {date} by NEFT from "
        "{merchant}. Avl Bal Rs {bal} -SBI",
        "CREDIT",
        "NETBANKING",
        merchant_from="merchant",
    ),
    T(
        "icici_upi_debit",
        "ICICIT",
        None,
        "ICICI Bank Acct XX{acct} debited for Rs {amount} on {date}; {merchant} credited. "
        "UPI:{ref}. Call 18002662 for dispute.",
        "DEBIT",
        "UPI",
        merchant_from="merchant",
    ),
    T(
        "icici_card",
        "ICICIT",
        None,
        "INR {amount} spent on ICICI Bank Card XX{acct} on {date} at {merchant}. "
        "Avl Limit: INR {bal}",
        "DEBIT",
        "CARD",
        merchant_from="merchant",
    ),
    T(
        "axis_upi_debit",
        "AXISBK",
        None,
        "INR {amount} debited\nA/c no. XX{acct}\n{date}\nUPI/P2M/{ref}/{merchant}\n"
        "Not you? SMS BLOCKUPI to 919951860002",
        "DEBIT",
        "UPI",
        merchant_from="merchant",
    ),
    T(
        "axis_imps_credit",
        "AXISBK",
        None,
        "Amt Rs {amount} credited to A/c no. XX{acct} via IMPS. Ref {ref}",
        "CREDIT",
        "NETBANKING",
    ),
    T(
        "kotak_upi_debit",
        "KOTAKB",
        None,
        "Sent Rs.{amount} from Kotak Bank AC X{acct} to {vpa} on {date}.UPI Ref {ref}. "
        "Not you, kotak.com/fraud",
        "DEBIT",
        "UPI",
        merchant_from="vpa",
    ),
    T(
        "kotak_failed",
        "KOTAKB",
        None,
        "Your transaction of Rs.{amount} to {vpa} has FAILED. Amount if debited will be refunded "
        "within {days} working days. UPI Ref {ref}",
        "DEBIT",
        "UPI",
        "FAILED",
        merchant_from="vpa",
    ),
    T(
        "pnb_debit",
        "PNBSMS",
        None,
        "Ac XX{acct} debited INR {amount} on {date} thru UPI ref {ref}. Bal INR {bal} -PNB",
        "DEBIT",
        "UPI",
    ),
    T(
        "bob_credit",
        "BOBTXN",
        None,
        "Rs.{amount} Credited to A/c ...{acct} thru UPI/{ref} by {vpa}. Total Bal:Rs.{bal}CR. "
        "-Bank of Baroda",
        "CREDIT",
        "UPI",
        merchant_from="vpa",
    ),
    T(
        "yes_pending",
        "YESBNK",
        None,
        "Rs {amount} is pending for UPI txn to {vpa}. Status will update shortly. Ref {ref}",
        "DEBIT",
        "UPI",
        "PENDING",
        merchant_from="vpa",
    ),
    T(
        "idfc_emi",
        "IDFCFB",
        None,
        "Rs.{amount} debited from your a/c **{acct} for EMI towards {merchant}. Ref {ref}",
        "DEBIT",
        "NETBANKING",
        merchant_from="merchant",
    ),
    T(
        "indus_refund",
        "INDUSB",
        None,
        "Refund of INR {amount} for UPI txn {ref} has been credited to your A/c XX{acct}",
        "CREDIT",
        "UPI",
        "REVERSED",
    ),
    T(
        "canara_debit",
        "CANBNK",
        None,
        "An amount of INR {amount} has been DEBITED to your account XXX{acct} on {date} towards "
        "UPI. Total Avail.bal INR {bal}. - Canara Bank",
        "DEBIT",
        "UPI",
    ),
    T(
        "union_credit",
        "UBOI",
        None,
        "A/c *{acct} Credited for Rs:{amount} on {date} by Mob Bk ref no {ref} Avl Bal Rs:{bal}",
        "CREDIT",
        "NETBANKING",
    ),
    T(
        "phonepe_paid",
        None,
        PHONEPE,
        "Paid ₹{amount} to {merchant}. UPI Ref No. {ref}",
        "DEBIT",
        "UPI",
        merchant_from="merchant",
    ),
    T(
        "phonepe_received",
        None,
        PHONEPE,
        "Received ₹{amount} from {person}",
        "CREDIT",
        "UPI",
        merchant_from="person",
    ),
    T(
        "gpay_paid",
        None,
        GPAY,
        "₹{amount} paid to {merchant}",
        "DEBIT",
        "UPI",
        merchant_from="merchant",
    ),
    T(
        "gpay_received",
        None,
        GPAY,
        "You received ₹{amount} from {person}",
        "CREDIT",
        "UPI",
        merchant_from="person",
    ),
    T(
        "paytm_paid",
        None,
        PAYTM,
        "Paid Rs.{amount} to {merchant} via Paytm UPI. Ref: {ref}",
        "DEBIT",
        "UPI",
        merchant_from="merchant",
    ),
    T(
        "paytm_wallet",
        None,
        PAYTM,
        "Rs.{amount} paid from Paytm Wallet to {merchant}. Wallet balance Rs.{bal}",
        "DEBIT",
        "WALLET",
        merchant_from="merchant",
    ),
    T(
        "amazon_pay",
        None,
        AMAZON,
        "Payment of ₹{amount} to {merchant} using Amazon Pay UPI was successful",
        "DEBIT",
        "UPI",
        merchant_from="merchant",
    ),
    T(
        "cred_bill",
        None,
        CRED,
        "₹{amount} paid towards your credit card bill ending {acct}. Ref {ref}",
        "DEBIT",
        "NETBANKING",
    ),
    T(
        "bhim_failed",
        None,
        BHIM,
        "Transaction of Rs {amount} to {vpa} failed. Ref {ref}",
        "DEBIT",
        "UPI",
        "FAILED",
        merchant_from="vpa",
    ),
)

# Banks held out of training entirely: only ever in gold_test (§15.3 "held-out banks").
HELD_OUT_SENDERS = frozenset({"CANBNK", "UBOI"})

MERCHANTS = (
    "Zomato",
    "Swiggy",
    "BLINKIT",
    "Zepto",
    "AMAZON",
    "FLIPKART",
    "Myntra",
    "BigBasket",
    "Uber India",
    "OLA",
    "Rapido",
    "IRCTC",
    "BookMyShow",
    "Netflix",
    "Spotify",
    "Jio Prepaid",
    "Airtel",
    "BESCOM",
    "Tata Power",
    "Apollo Pharmacy",
    "DMART",
    "Reliance Smart",
    "Chai Point",
    "Third Wave Coffee",
    "McDonalds",
    "Dominos",
    "KFC",
    "Haldiram",
    "Croma",
    "Decathlon",
    "Nykaa",
    "PharmEasy",
    "Bajaj Finance",
    "ACME TECH PVT LTD",
    "Urban Company",
)
VPA_HANDLES = ("ybl", "okaxis", "okhdfcbank", "paytm", "icici", "hdfcbank", "axl", "ibl")
MERCHANT_VPAS = (
    "zomato",
    "swiggy.stores",
    "blinkit",
    "zepto.payu",
    "amazonpay",
    "flipkart",
    "uber",
    "olacabs",
    "irctc",
    "bookmyshow",
    "netflix",
    "jiomart",
    "paytmqr281005050101",
    "bharatpe",
)
HINDI_WORDS = ("भुगतान सफल", "धन्यवाद", "राशि", "")
EMOJI = ("✅", "💸", "🎉", "")


def indian_grouping(rupees: int) -> str:
    digits = str(rupees)
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def amount_text(paise: int, rng: random.Random) -> str:
    """Amount as banks write it: ``349``, ``349.00``, ``1,249.50``, ``1,00,000.00``, ``1249.5``."""
    rupees, rem = divmod(paise, 100)
    style = rng.random()
    whole = indian_grouping(rupees) if style < 0.6 or rupees >= 100_000 else str(rupees)
    if rem == 0 and style < 0.35:
        return whole
    if rem % 10 == 0 and style > 0.9:
        return f"{whole}.{rem // 10}"
    return f"{whole}.{rem:02d}"


def _paise(rng: random.Random) -> int:
    bucket = rng.random()
    if bucket < 0.5:
        rupees = rng.randint(10, 999)
    elif bucket < 0.85:
        rupees = rng.randint(1_000, 19_999)
    else:
        rupees = rng.randint(20_000, 250_000)
    cents = 0 if rng.random() < 0.6 else rng.randint(0, 99)
    return rupees * 100 + cents


def _date(rng: random.Random) -> str:
    day, month = rng.randint(1, 28), rng.randint(1, 12)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return rng.choice(
        [
            f"{day:02d}-{month:02d}-26",
            f"{day:02d}{months[month - 1]}26",
            f"{day:02d}-{months[month - 1]}-26",
        ]
    )


def _noise(text: str, rng: random.Random) -> str:
    r = rng.random()
    if r < 0.15:
        text = text.replace(". ", ".  ")
    elif r < 0.25:
        text = text.replace(". ", ".\n", 1)
    if rng.random() < 0.1:
        text = f"{rng.choice(EMOJI)} {text}".strip()
    if rng.random() < 0.08:
        text = f"{text} {rng.choice(HINDI_WORDS)}".strip()
    return text


def _person_vpa(rng: random.Random) -> str:
    return f"p{rng.getrandbits(32):08x}@{rng.choice(VPA_HANDLES)}"


def render(template: Template, rng: random.Random, index: int) -> LabeledExample:
    paise = _paise(rng)
    values = {
        "amount": amount_text(paise, rng),
        "acct": f"{rng.randint(0, 9999):04d}",
        "vpa": f"{rng.choice(MERCHANT_VPAS)}@{rng.choice(VPA_HANDLES)}",
        "merchant": rng.choice(MERCHANTS),
        "person": _person_vpa(rng),
        "ref": str(rng.randint(10**11, 10**12 - 1)),
        "date": _date(rng),
        "bal": amount_text(rng.randint(10_000, 50_000_000), rng),
        "days": str(rng.choice([3, 5, 7])),
    }
    text = _noise(template.text.format(**values), rng)
    merchant = values.get(template.merchant_from or "") if template.merchant_from else None
    label = Extraction(
        is_transaction=True,
        amount=values["amount"],
        direction=template.direction,  # type: ignore[arg-type]
        channel=template.channel,  # type: ignore[arg-type]
        status=template.status,  # type: ignore[arg-type]
        merchant_raw=merchant,
        counterparty_vpa=values[template.merchant_from]
        if template.merchant_from in {"vpa", "person"}
        else None,
        reference_id=values["ref"] if "ref" in template.fields else None,
        account_hint=values["acct"] if "acct" in template.fields else None,
        balance_after=values["bal"]
        if "bal" in template.fields and template.channel not in {"CARD"}
        else None,
        promised_refund_days=int(values["days"])
        if template.status == "FAILED" and "days" in template.fields
        else None,
    )
    sender = (
        f"{rng.choice(['AX', 'VM', 'JD', 'AD', 'BZ', 'VK'])}-{template.sender}"
        if template.sender
        else None
    )
    return LabeledExample(
        event_id=f"syn-{template.id}-{index:06d}",
        sender=sender,
        source_app=template.app,
        text=text,
        source="SYNTHETIC",
        label=label,
        reviewed=True,  # labels are exact by construction
        # The real template id: noise and mixed-case merchant names would otherwise give one
        # template several signatures and leak it across splits.
        template=f"syn:{template.id}",
    )


NEGATIVE_TEMPLATES: tuple[tuple[str, str | None, str | None, str], ...] = (
    (
        "otp",
        "HDFCBK",
        None,
        "{otp} is OTP for txn of Rs {amount} at {merchant} on HDFC Bank card XX{acct}. Valid for 5 mins. Do not share",
    ),
    (
        "otp_sbi",
        "SBIOTP",
        None,
        "OTP for your transaction of INR {amount} is {otp}. Never share OTP with anyone -SBI",
    ),
    (
        "promo",
        "SBIINB",
        None,
        "Get cashback up to Rs {amount} on your next bill payment via YONO! T&C apply",
    ),
    (
        "promo_loan",
        "KOTAKB",
        None,
        "Congrats! You are pre-approved for a personal loan up to Rs {amount}. Apply now",
    ),
    ("balance", "ICICIT", None, "Avl Bal in A/c XX{acct} is INR {amount} as on {date}"),
    ("collect", None, PHONEPE, "{person} has requested money Rs {amount} from you. Pay now?"),
    (
        "statement",
        "HDFCBK",
        None,
        "Your HDFC Bank credit card statement for Sep is ready. Total due Rs {amount}, min due Rs {bal}, due date {date}",
    ),
    ("offer_app", None, GPAY, "Scratch to win up to ₹{amount} cashback on your next recharge"),
    (
        "reminder",
        "AXISBK",
        None,
        "Reminder: EMI of Rs {amount} for loan XX{acct} is due on {date}. Keep sufficient balance.",
    ),
)


def render_negative(
    kind: tuple[str, str | None, str | None, str], rng: random.Random, index: int
) -> LabeledExample:
    name, header, app, fmt = kind
    values = {
        "otp": f"{rng.randint(0, 999999):06d}",
        "amount": amount_text(_paise(rng), rng),
        "merchant": rng.choice(MERCHANTS),
        "acct": f"{rng.randint(0, 9999):04d}",
        "date": _date(rng),
        "bal": amount_text(rng.randint(10_000, 500_000), rng),
        "person": _person_vpa(rng),
    }
    text = _noise(fmt.format(**values), rng)
    sender = f"{rng.choice(['AX', 'VM', 'JD'])}-{header}" if header else None
    return LabeledExample(
        event_id=f"neg-{name}-{index:06d}",
        sender=sender,
        source_app=app,
        text=text,
        source="HARD_NEGATIVE",
        label=Extraction(is_transaction=False),
        reviewed=True,
        template=f"neg:{name}",
    )


def generate(n: int, seed: int = 0, negative_share: float = 0.2) -> Iterator[LabeledExample]:
    """``n`` examples: positives spread over templates, ``negative_share`` hard negatives."""
    rng = random.Random(seed)
    for i in range(n):
        if rng.random() < negative_share:
            yield render_negative(rng.choice(NEGATIVE_TEMPLATES), rng, i)
        else:
            yield render(rng.choice(TEMPLATES), rng, i)

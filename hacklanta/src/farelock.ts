export type FareLockQuote = {
  origin: string;
  destination: string;
  departure_date: string;
  fare_paid: number;
  flight_number: string | null;
  fee: number;
  max_potential_payout: number;
  protection_days: number;
  currency: "USD";
};

export type FareLockPurchase = {
  id: number;
  origin: string;
  destination: string;
  departure_date: string;
  strike_cents: number;
  fee_cents: number;
  max_payout_cents: number;
  protection_days: number;
  status: string;
  balance_cents: number;
  currency: "USD";
};

type Fetcher = (input: string | URL, init?: RequestInit) => Promise<Response>;

type FareLockErrorDetail = {
  code?: string;
  message?: string;
  balance_cents?: number;
  fee_cents?: number;
  shortfall_cents?: number;
};

export class FareLockApiError extends Error {
  constructor(
    message: string,
    readonly code?: string,
    readonly detail: FareLockErrorDetail = {},
  ) {
    super(message);
    this.name = "FareLockApiError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function errorDetail(value: unknown): FareLockErrorDetail {
  if (!isRecord(value) || !isRecord(value.detail)) return {};
  return {
    code: typeof value.detail.code === "string" ? value.detail.code : undefined,
    message:
      typeof value.detail.message === "string"
        ? value.detail.message
        : undefined,
    balance_cents: typeof value.detail.balance_cents === "number"
      ? value.detail.balance_cents
      : undefined,
    fee_cents: typeof value.detail.fee_cents === "number"
      ? value.detail.fee_cents
      : undefined,
    shortfall_cents: typeof value.detail.shortfall_cents === "number"
      ? value.detail.shortfall_cents
      : undefined,
  };
}

function isFareLockQuote(value: unknown): value is FareLockQuote {
  return (
    isRecord(value) &&
    typeof value.origin === "string" &&
    typeof value.destination === "string" &&
    typeof value.fare_paid === "number" &&
    typeof value.fee === "number" &&
    typeof value.max_potential_payout === "number"
  );
}

function isFareLockPurchase(value: unknown): value is FareLockPurchase {
  return isRecord(value) &&
    typeof value.id === "number" &&
    typeof value.origin === "string" &&
    typeof value.destination === "string" &&
    typeof value.strike_cents === "number" &&
    typeof value.fee_cents === "number" &&
    typeof value.balance_cents === "number";
}

export async function fetchFareLockQuote(
  query: string,
  apiUrl = process.env.FARELOCK_API_URL ?? "http://127.0.0.1:8000",
  fetcher: Fetcher = fetch,
): Promise<FareLockQuote> {
  const endpoint = new URL("/quotes/farelock", apiUrl);
  let response: Response;

  try {
    response = await fetcher(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query }),
    });
  } catch {
    throw new FareLockApiError(
      "FareLock is temporarily unavailable. Please try again in a moment.",
    );
  }

  const payload: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = errorDetail(payload);
    throw new FareLockApiError(
      detail.message ?? "FareLock could not price this trip right now.",
      detail.code,
      detail,
    );
  }
  if (!isFareLockQuote(payload)) {
    throw new FareLockApiError(
      "FareLock returned an invalid quote. Please try again in a moment.",
    );
  }
  return payload;
}

export async function purchaseFareLockFromPhoton(
  phoneNumber: string,
  query: string,
  apiUrl = process.env.FARELOCK_API_URL ?? "http://127.0.0.1:8000",
  secret = process.env.PHOTON_INTERNAL_SECRET ?? process.env.PHOTON_MESSAGING_SECRET,
  fetcher: Fetcher = fetch,
): Promise<FareLockPurchase> {
  if (!secret) {
    throw new FareLockApiError("FareLock purchasing is not configured.");
  }

  let response: Response;
  try {
    response = await fetcher(new URL("/locks/farelock/photon-purchase", apiUrl), {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-photon-internal-secret": secret,
      },
      body: JSON.stringify({ phone_number: phoneNumber, query }),
    });
  } catch {
    throw new FareLockApiError(
      "FareLock is temporarily unavailable. Please try again in a moment.",
    );
  }

  const payload: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail = errorDetail(payload);
    throw new FareLockApiError(
      detail.message ?? "FareLock could not complete your purchase right now.",
      detail.code,
      detail,
    );
  }
  if (!isFareLockPurchase(payload)) {
    throw new FareLockApiError(
      "FareLock returned an invalid purchase confirmation. Please try again.",
    );
  }
  return payload;
}

function formatDollars(amount: number, fractionDigits?: number): string {
  const digits = fractionDigits ?? (Number.isInteger(amount) ? 0 : 2);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(amount);
}

export function formatFareLockQuote(quote: FareLockQuote): string {
  return [
    `FareLock for your ${formatDollars(quote.fare_paid)} ${quote.origin} → ${quote.destination} flight costs ${formatDollars(quote.fee, 2)}.`,
    `If the fare drops before your trip, we’ll cover the difference—up to ${formatDollars(quote.max_potential_payout)}. Reply LOCK within 5 minutes to purchase.`,
  ].join("\n\n");
}

export function formatFareLockPurchase(purchase: FareLockPurchase): string {
  return [
    `Locked — your ${formatDollars(purchase.strike_cents / 100)} ${purchase.origin} → ${purchase.destination} fare is protected until departure.`,
    `We charged ${formatDollars(purchase.fee_cents / 100, 2)}. Your FareLocker balance is ${formatDollars(purchase.balance_cents / 100, 2)}.`,
  ].join("\n\n");
}

export function formatFareLockError(error: unknown): string {
  if (!(error instanceof FareLockApiError)) {
    return "FareLock is temporarily unavailable. Please try again in a moment.";
  }

  switch (error.code) {
    case "missing_route":
      return "Tell me where you’re flying, like “Atlanta to LAX.”";
    case "missing_departure_date":
      return "What day is your flight? You can say “next Friday.”";
    case "missing_fare_paid":
      return "How much did you pay? Include the fare, like “for $284.”";
    case "phone_not_linked":
      return "This iMessage number is not linked to a FareLocker account. Add it to your account, then try again.";
    case "insufficient_funds": {
      const { balance_cents, fee_cents, shortfall_cents } = error.detail;
      if (balance_cents !== undefined && fee_cents !== undefined && shortfall_cents !== undefined) {
        return `Your FareLock costs ${formatDollars(fee_cents / 100, 2)}, but your FareLocker balance is ${formatDollars(balance_cents / 100, 2)}. Add ${formatDollars(shortfall_cents / 100, 2)} or more, then reply LOCK again.`;
      }
      return "Your FareLocker balance is too low to purchase this FareLock.";
    }
    default:
      return error.message;
  }
}

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

type Fetcher = (input: string | URL, init?: RequestInit) => Promise<Response>;

type FareLockErrorDetail = {
  code?: string;
  message?: string;
};

export class FareLockApiError extends Error {
  constructor(
    message: string,
    readonly code?: string,
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
    );
  }
  if (!isFareLockQuote(payload)) {
    throw new FareLockApiError(
      "FareLock returned an invalid quote. Please try again in a moment.",
    );
  }
  return payload;
}

function formatDollars(amount: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

export function formatFareLockQuote(quote: FareLockQuote): string {
  return [
    `FareLock for your ${formatDollars(quote.fare_paid)} ${quote.origin} → ${quote.destination} flight costs ${formatDollars(quote.fee)}.`,
    `If the fare drops before your trip, we’ll cover the difference—up to ${formatDollars(quote.max_potential_payout)}.`,
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
    default:
      return error.message;
  }
}

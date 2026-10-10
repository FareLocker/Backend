import { describe, expect, test } from "bun:test";

import {
  FareLockApiError,
  fetchFareLockQuote,
  formatFareLockError,
  formatFareLockQuote,
} from "./farelock";

const quote = {
  origin: "ATL",
  destination: "LAX",
  departure_date: "2026-10-16",
  fare_paid: 284,
  flight_number: null,
  fee: 14.6,
  max_potential_payout: 142,
  protection_days: 6,
  currency: "USD" as const,
};

describe("FareLock messaging", () => {
  test("formats the concise price-drop-protection quote", () => {
    expect(formatFareLockQuote(quote)).toBe(
      "FareLock for your $284.00 ATL → LAX flight costs $14.60.\n\n" +
        "If the fare drops before your trip, we’ll cover the difference—up to $142.00.",
    );
  });

  test("forwards the traveler’s natural-language message to the quote API", async () => {
    let requestUrl = "";
    let requestBody = "";
    const fetcher = async (input: string | URL, init?: RequestInit) => {
      requestUrl = String(input);
      requestBody = String(init?.body);
      return Response.json(quote);
    };

    await expect(
      fetchFareLockQuote(
        "I booked from Atlanta to LAX next Friday for $284. How much is the FareLock?",
        "https://api.example.test/v1",
        fetcher,
      ),
    ).resolves.toEqual(quote);
    expect(requestUrl).toBe("https://api.example.test/quotes/farelock");
    expect(JSON.parse(requestBody)).toEqual({
      query: "I booked from Atlanta to LAX next Friday for $284. How much is the FareLock?",
    });
  });

  test("turns missing fare information into a natural follow-up", () => {
    expect(
      formatFareLockError(
        new FareLockApiError("Include the amount paid.", "missing_fare_paid"),
      ),
    ).toBe("How much did you pay? Include the fare, like “for $284.”");
  });
});

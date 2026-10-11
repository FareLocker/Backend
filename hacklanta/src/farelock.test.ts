import { describe, expect, test } from "bun:test";

import {
  FareLockApiError,
  fetchFareLockQuote,
  formatFareLockError,
  formatFareLockPurchase,
  formatFareLockQuote,
  purchaseFareLockFromPhoton,
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
      "FareLock for your $284 ATL → LAX flight costs $14.60.\n\n" +
        "If the fare drops before your trip, we’ll cover the difference—up to $142. Reply LOCK within 5 minutes to purchase.",
    );
  });

  test("purchases from the account linked to the iMessage sender", async () => {
    let requestBody = "";
    const fetcher = async (_input: string | URL, init?: RequestInit) => {
      requestBody = String(init?.body);
      return Response.json({
        id: 8, origin: "ATL", destination: "LAX", departure_date: "2026-10-16",
        flight_number: "DL123",
        strike_cents: 28400, fee_cents: 1460, max_payout_cents: 14200,
        protection_days: 6, status: "open", balance_cents: 8540, currency: "USD",
      });
    };

    const purchase = await purchaseFareLockFromPhoton(
      "+15551234567", "I booked ATL to LAX for $284", "https://api.example.test", "secret", fetcher,
    );
    expect(JSON.parse(requestBody)).toEqual({
      phone_number: "+15551234567", query: "I booked ATL to LAX for $284",
    });
    expect(purchase.flight_number).toBe("DL123");
    expect(formatFareLockPurchase(purchase)).toContain("Locked — your DL123 $284 ATL → LAX");
  });

  test("keeps the confirmation natural when no flight number was provided", () => {
    expect(formatFareLockPurchase({
      id: 8, origin: "ATL", destination: "LAX", departure_date: "2026-10-16",
      flight_number: null, strike_cents: 28400, fee_cents: 1460,
      max_payout_cents: 14200, protection_days: 6, status: "open",
      balance_cents: 8540, currency: "USD",
    })).toContain("Locked — your $284 ATL → LAX");
  });

  test("explains the amount needed when the linked account is short", () => {
    expect(formatFareLockError(new FareLockApiError("too low", "insufficient_funds", {
      balance_cents: 800, fee_cents: 1250, shortfall_cents: 450,
    }))).toBe(
      "Your FareLock costs $12.50, but your FareLocker balance is $8.00. Add $4.50 or more, then reply LOCK again.",
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

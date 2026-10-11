import { GoogleGenAI, Type, type Schema, type Tool } from "@google/genai/node";
import type { PhotonLockRequest } from "./farelock";

/*
 * FareLocker's iMessage assistant: Gemini with tools that call the API.
 *
 * Tool → API route (all documented at /docs):
 *   search_flights      POST /fares/search
 *   get_farelock_quote  POST /quotes/farelock/price
 *   prepare_farelock    POST /quotes/farelock/price, then saves the quote
 *   my_locks            GET  /photon/users/{phone}/locks   (shared secret)
 *   my_wallet           GET  /photon/users/{phone}/wallet  (shared secret)
 *
 * Gemini never buys anything. prepare_farelock only hands the quote to
 * onFareLockPrepared; index.ts buys it when the texter replies LOCK. Tools
 * about "the user" always use the texter's own phone, never one Gemini picks.
 */

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
// A "lite" model on purpose: replies in under a second per call, versus ~12s
// for the full Flash models, which think at length before answering. Texting
// needs speed far more than depth. Override with GEMINI_MODEL.
const model = process.env.GEMINI_MODEL ?? "gemini-3.1-flash-lite";

const apiUrl = process.env.FARELOCK_API_URL ?? "http://127.0.0.1:8000";
const secret = process.env.PHOTON_INTERNAL_SECRET ?? process.env.PHOTON_MESSAGING_SECRET;

// Fast, predictable defaults for text conversations. These stay in code so
// every deployment behaves the same without extra environment configuration.
const FLIGHT_SEARCH_CACHE_TTL_MS = 45_000;
const FLIGHT_SEARCH_CACHE_MAX_ENTRIES = 200;
const MAX_HISTORY_CONTENTS = 12;
const MAX_AGENT_TOOL_LOOPS = 3;

const instructions = () => `You are FareLocker's assistant, texting over iMessage.
FareLocker sells FareLocks: after booking a flight, you pay a small fee, and if
the fare drops before you fly, FareLocker pays you the difference (up to a limit).
Locks are sold on nonstop flights between Atlanta (ATL) and New York JFK, Los
Angeles (LAX), Miami (MIA) or Chicago O'Hare (ORD), either direction, 1 to 30
days before departure.

Rules:
- Keep replies short and friendly, like a text message. No markdown.
- Never make up prices, flights, balances or lock details: use a tool and only
  state what it returned. If a tool returns an error, say so plainly and don't guess.
- To quote or lock a fare you need route, date, flight number and what they paid.
  Ask for whatever is missing first.
- You cannot buy anything. When they want a lock, call prepare_farelock, then
  tell them the fee and to reply LOCK within 5 minutes to pay from their wallet.
- If someone asks how to get FareLocker texts or connect their account, tell them
  to sign up on the FareLocker website with their phone number, then text START
  to the number shown on their account's Connect page.
- Today's date is ${new Date().toISOString().slice(0, 10)}.`;

const flightParameters: Schema = {
  type: Type.OBJECT,
  properties: {
    origin: { type: Type.STRING, description: "Origin airport code, e.g. ATL" },
    destination: { type: Type.STRING, description: "Destination airport code, e.g. LAX" },
    date: { type: Type.STRING, description: "Departure date, YYYY-MM-DD" },
    flightNumber: { type: Type.STRING, description: "e.g. DL123" },
    pricePaid: { type: Type.NUMBER, description: "Price the user paid, in USD" },
  },
  required: ["origin", "destination", "date", "flightNumber", "pricePaid"],
};

const tools: Tool[] = [
  {
    functionDeclarations: [
      {
        name: "search_flights",
        description:
          "Find nonstop flights and current prices, with each flight's FareLock fee. " +
          "The query can be a route, one airport, a flight number and/or a date, e.g. " +
          "'atl to lax friday', 'DL 1234 oct 20', 'from atlanta tomorrow'.",
        parameters: {
          type: Type.OBJECT,
          properties: { query: { type: Type.STRING, description: "The search, in words." } },
          required: ["query"],
        },
      },
      {
        name: "get_farelock_quote",
        description: "Gets a FareLock quote (fee and maximum payout) for a booked flight.",
        parameters: flightParameters,
      },
      {
        name: "prepare_farelock",
        description:
          "Sets up a FareLock purchase when the user says they want one. The user must " +
          "then reply LOCK within 5 minutes to pay.",
        parameters: flightParameters,
      },
      {
        name: "my_locks",
        description: "Lists the user's own FareLocks and their status.",
        parameters: { type: Type.OBJECT, properties: {} },
      },
      {
        name: "my_wallet",
        description: "Gets the user's FareLocker wallet balance.",
        parameters: { type: Type.OBJECT, properties: {} },
      },
    ],
  },
];

export type AgentInput = {
  /** One conversation per texter, so follow-ups ("what about Saturday?") work. */
  chatId: string;
  /** The texter's phone (E.164), for tools about their account. null if unknown. */
  phone: string | null;
  /** What they just texted. */
  text: string;
  /** Called by prepare_farelock; index.ts buys it when the texter replies LOCK. */
  onFareLockPrepared: (request: PhotonLockRequest) => void;
};

/** Calls the FareLocker API. Errors come back as { error } for Gemini to explain. */
async function callApi(path: string, init: RequestInit = {}): Promise<any> {
  const startedAt = Date.now();
  const route = path.replace(/\/photon\/users\/[^/]+/, "/photon/users/:phone");
  const method = init.method ?? "GET";
  try {
    const r = await fetch(new URL(path, apiUrl), {
      ...init,
      headers: { "Content-Type": "application/json", ...init.headers },
      signal: AbortSignal.timeout(30_000),
    });
    const body: any = await r.json().catch(() => undefined);
    console.info("farelock.api.completed", {
      route,
      method,
      status: r.status,
      durationMs: Date.now() - startedAt,
    });
    if (!r.ok) {
      const detail = body?.detail;
      const message =
        typeof detail === "string" ? detail : detail?.message ?? `FareLock API returned ${r.status}`;
      return { error: message, code: detail?.code ?? null };
    }
    return body;
  } catch (error) {
    console.warn("farelock.api.failed", {
      route,
      method,
      durationMs: Date.now() - startedAt,
      error: error instanceof Error ? error.name : "unknown",
    });
    return { error: "FareLock API unreachable" };
  }
}

const dollars = (cents: number) => Math.round(cents) / 100;

/** The API's structured lock request, from Gemini's flight arguments. */
function lockRequest(args: any): PhotonLockRequest {
  return {
    origin: String(args.origin ?? "").toUpperCase(),
    destination: String(args.destination ?? "").toUpperCase(),
    departure_date: String(args.date ?? ""),
    flight_number: String(args.flightNumber ?? ""),
    fare_cents: Math.round(Number(args.pricePaid) * 100),
  };
}

/** A quote from POST /quotes/farelock/price, in dollars for Gemini. */
async function quote(request: PhotonLockRequest) {
  const q = await callApi("/quotes/farelock/price", {
    method: "POST",
    body: JSON.stringify(request),
  });
  if (q.error) return q;
  return {
    route: `${q.origin} to ${q.destination}`,
    date: q.departure_date,
    flight: q.flight_number,
    fare_paid_usd: dollars(q.fare_cents),
    fee_usd: dollars(q.fee_cents),
    max_payout_usd: dollars(q.max_payout_cents),
    protection_days: q.protection_days,
  };
}

const userHeaders = () => ({ "X-Photon-Internal-Secret": secret ?? "" });
const notLinked = { error: "This number isn't linked to a FareLocker account." };

type CachedFlightSearch = { value: unknown; expiresAt: number };
const flightSearchCache = new Map<string, CachedFlightSearch>();
const flightSearchInFlight = new Map<string, Promise<unknown>>();

const normalizeFlightSearch = (query: string) => query.trim().toLowerCase().replace(/\s+/g, " ");

function cacheFlightSearch(key: string, value: unknown): void {
  if (flightSearchCache.size >= FLIGHT_SEARCH_CACHE_MAX_ENTRIES) {
    const oldestKey = flightSearchCache.keys().next().value;
    if (oldestKey) flightSearchCache.delete(oldestKey);
  }
  flightSearchCache.set(key, { value, expiresAt: Date.now() + FLIGHT_SEARCH_CACHE_TTL_MS });
}

async function searchFlights(query: string): Promise<unknown> {
  const key = normalizeFlightSearch(query);
  const cached = flightSearchCache.get(key);
  if (cached && cached.expiresAt > Date.now()) {
    console.info("farelock.flight_search.cache_hit", { queryLength: key.length });
    return cached.value;
  }
  if (cached) flightSearchCache.delete(key);

  const pending = flightSearchInFlight.get(key);
  if (pending) {
    console.info("farelock.flight_search.in_flight_shared", { queryLength: key.length });
    return pending;
  }

  console.info("farelock.flight_search.cache_miss", { queryLength: key.length });
  const request = (async () => {
    const data = await callApi("/fares/search", {
      method: "POST",
      body: JSON.stringify({ query }),
    });
    if (data.error) return data;

    // Just the cheapest few nonstops, so replies stay short.
    const results = (data.results as any[])
      .filter((f) => f.stop_airports.length === 0)
      .sort((a, b) => a.fare_cents - b.fare_cents)
      .slice(0, 5)
      .map((f) => ({
        flight: `${f.airline_name} ${f.flight_number}`,
        route: `${f.depart.airport_code} to ${f.arrive.airport_code}`,
        departs: f.depart.local_time,
        price_usd: dollars(f.fare_cents),
        farelock_fee_usd: f.lock ? dollars(f.lock.fee_cents) : null,
      }));

    if (results.length > 0) cacheFlightSearch(key, results);
    return results;
  })();
  flightSearchInFlight.set(key, request);

  try {
    return await request;
  } finally {
    if (flightSearchInFlight.get(key) === request) flightSearchInFlight.delete(key);
  }
}

const handlers: Record<string, (args: any, input: AgentInput) => Promise<unknown>> = {
  search_flights: async (args) => {
    return searchFlights(String(args.query ?? ""));
  },

  get_farelock_quote: (args) => quote(lockRequest(args)),

  prepare_farelock: async (args, input) => {
    if (!input.phone) return notLinked;
    const request = lockRequest(args);
    const result = await quote(request);
    if ("error" in result) return result;
    input.onFareLockPrepared(request);
    return { ...result, next_step: "Tell the user to reply LOCK within 5 minutes to pay." };
  },

  my_locks: async (_args, input) => {
    if (!input.phone) return notLinked;
    const data = await callApi(`/photon/users/${encodeURIComponent(input.phone)}/locks`, {
      headers: userHeaders(),
    });
    if (data.error) return data;
    return (data.locks as any[]).map((l) => ({
      id: l.id,
      route: `${l.origin} to ${l.destination}`,
      date: l.departure_date,
      flight: l.flight_number,
      locked_fare_usd: dollars(l.strike_cents),
      status: l.status,
      payout_usd: l.payout_cents === null ? null : dollars(l.payout_cents),
    }));
  },

  my_wallet: async (_args, input) => {
    if (!input.phone) return notLinked;
    const data = await callApi(`/photon/users/${encodeURIComponent(input.phone)}/wallet`, {
      headers: userHeaders(),
    });
    if (data.error) return data;
    return { balance_usd: dollars(data.balance_cents) };
  },
};

const history = new Map<string, any[]>();

export async function runAgent(input: AgentInput): Promise<string> {
  const contents = history.get(input.chatId) ?? [];
  contents.push({ role: "user", parts: [{ text: input.text }] });
  const startedAt = Date.now();
  let generations = 0;
  let outcome = "failed";

  try {
    for (let i = 0; i < MAX_AGENT_TOOL_LOOPS; i++) {
      const generationStartedAt = Date.now();
      generations++;
      let response;
      try {
        response = await ai.models.generateContent({
          model,
          contents,
          config: { systemInstruction: instructions(), tools },
        });
      } catch (error) {
        console.warn("farelock.gemini.failed", {
          model,
          loop: i + 1,
          durationMs: Date.now() - generationStartedAt,
          error: error instanceof Error ? error.name : "unknown",
        });
        throw error;
      }

      const calls = response.functionCalls;
      console.info("farelock.gemini.completed", {
        model,
        loop: i + 1,
        durationMs: Date.now() - generationStartedAt,
        functionCalls: calls?.length ?? 0,
      });
      if (!calls?.length) {
        const reply = response.text ?? "Sorry, I didn't catch that.";
        contents.push({ role: "model", parts: [{ text: reply }] });
        outcome = "reply";
        return reply;
      }

      const content = response.candidates?.[0]?.content;
      if (content) contents.push(content);

      const parts = await Promise.all(
        calls.map(async (c) => {
          const fn = handlers[c.name!];
          const result = fn ? await fn(c.args ?? {}, input) : { error: "unknown tool" };
          return { functionResponse: { name: c.name!, response: { result } } };
        }),
      );
      contents.push({ role: "user", parts });
    }
    outcome = "tool_loop_limit";
    console.warn("farelock.agent.tool_loop_limit", { model, loops: MAX_AGENT_TOOL_LOOPS });
    return "Sorry, that took too many steps. Try again?";
  } finally {
    history.set(input.chatId, trimHistory(contents));
    console.info("farelock.agent.completed", {
      model,
      durationMs: Date.now() - startedAt,
      generations,
      outcome,
    });
  }
}

/**
 * The last few content items. Trimming must start at a plain user message: a history
 * that begins mid tool call (a tool result with no request) is rejected.
 */
function trimHistory(contents: any[]): any[] {
  const recent = contents.slice(-MAX_HISTORY_CONTENTS);
  const start = recent.findIndex(
    (turn) => turn.role === "user" && turn.parts?.some((part: any) => "text" in part),
  );
  return start === -1 ? [] : recent.slice(start);
}

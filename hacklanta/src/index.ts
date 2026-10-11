import { Spectrum } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";
import {
  fetchFareLockQuote,
  formatFareLockError,
  formatFareLockPurchase,
  formatFareLockQuote,
  purchaseFareLockFromPhoton,
} from "./farelock";
import { runAgent } from "./agent";

// Spectrum bridges a single agent loop to many messaging interfaces.
// Each provider in `providers` adds an interface (terminal TUI, iMessage, …).
// Docs: https://photon.codes/docs/spectrum-ts
const app = await Spectrum({
  projectId: process.env.PROJECT_ID!,
  projectSecret: process.env.PROJECT_SECRET!,
  providers: [
    // imessage
    imessage.config(),
  ],
});

// PHOTON_MESSAGING_SECRET is retained for existing deployments. New
// deployments should use PHOTON_INTERNAL_SECRET, which matches the API's name.
const messagingSecret =
  process.env.PHOTON_INTERNAL_SECRET ?? process.env.PHOTON_MESSAGING_SECRET;
const messagingPort = Number(process.env.PHOTON_MESSAGING_PORT ?? 8787);
const pendingQuotes = new Map<string, { query: string; expiresAt: number }>();
const quoteTtlMs = 5 * 60 * 1000;

const normalizePhone = (value: string): string | null => {
  const trimmed = value.trim();
  if (/^\+[1-9]\d{7,14}$/.test(trimmed)) return trimmed;
  const digits = trimmed.replace(/\D/g, "");
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits.startsWith("1")) return `+${digits}`;
  if (digits.length >= 8 && digits.length <= 15) return `+${digits}`;
  return null;
};

const phoneFromUnknown = (value: unknown): string | null => {
  if (typeof value !== "string" || !value.trim()) return null;
  const direct = normalizePhone(value);
  if (direct) return direct;
  const match = value.match(/\+[1-9]\d{7,14}/);
  return match ? match[0] : null;
};

/** Keep logs useful for support without exposing a traveler's full number. */
const redactPhone = (phoneNumber: string | null): string => {
  if (!phoneNumber) return "unknown";
  return `${phoneNumber.slice(0, 2)}••••••${phoneNumber.slice(-4)}`;
};

const phoneFromInbound = (space: unknown, message: unknown): string | null => {
  const spaceRecord = space as Record<string, unknown>;
  const messageRecord = message as Record<string, unknown>;
  const sender = messageRecord.sender as Record<string, unknown> | undefined;

  const candidates = [
    sender?.id,
    sender?.phone,
    sender?.phoneNumber,
    messageRecord.from,
    spaceRecord.id,
    spaceRecord.platformId,
    (spaceRecord.user as Record<string, unknown> | undefined)?.id,
    (spaceRecord.user as Record<string, unknown> | undefined)?.phoneNumber,
  ];

  for (const candidate of candidates) {
    const phone = phoneFromUnknown(candidate);
    if (phone) return phone;
  }
  return null;
};

const welcomeText = (firstName: string) =>
  `Welcome to FareLocker, ${firstName}! Your account is ready. ✈️`;

/** Enroll a shared-line user and return the line assigned by Spectrum. */
const enrollProjectUser = async (input: {
  phoneNumber: string;
  firstName: string;
  lastName?: string;
  email?: string;
}): Promise<{ photonUserId: string; assignedPhoneNumber: string }> => {
  const projectId = process.env.PROJECT_ID;
  const projectSecret = process.env.PROJECT_SECRET;
  if (!projectId || !projectSecret) {
    throw new Error("PROJECT_ID and PROJECT_SECRET are required to enroll users");
  }

  const body: Record<string, string> = {
    type: "shared",
    phoneNumber: input.phoneNumber,
    firstName: input.firstName,
  };
  if (input.lastName) body.lastName = input.lastName;
  if (input.email) body.email = input.email;

  const projectUser = await fetch(
    `https://spectrum.photon.codes/projects/${encodeURIComponent(projectId)}/users/`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Basic ${btoa(`${projectId}:${projectSecret}`)}`,
      },
      body: JSON.stringify(body),
    },
  );

  const responseText = await projectUser.text();
  let payload: unknown = null;
  try {
    payload = JSON.parse(responseText);
  } catch {
    // The diagnostic below includes the non-JSON response body.
  }
  const data = payload !== null && typeof payload === "object" && "data" in payload
    ? (payload as { data: unknown }).data
    : payload;
  const record = data !== null && typeof data === "object"
    ? data as Record<string, unknown>
    : null;
  const photonUserId = record?.id;
  const assignedPhoneNumber = record?.assignedPhoneNumber;
  if (!projectUser.ok || typeof photonUserId !== "string" || typeof assignedPhoneNumber !== "string") {
    const detail = responseText.slice(0, 300);
    throw new Error(
      `Could not provision Photon project user (HTTP ${projectUser.status}): ${detail}`,
    );
  }
  return { photonUserId, assignedPhoneNumber };
};

const provisionUser = async (request: Request): Promise<Response> => {
  if (!messagingSecret) {
    return Response.json(
      { error: "Photon messaging endpoint is not configured" },
      { status: 503 },
    );
  }
  if (request.headers.get("x-photon-internal-secret") !== messagingSecret) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: {
    phone_number?: unknown;
    first_name?: unknown;
    last_name?: unknown;
    email?: unknown;
  };
  try {
    const parsed: unknown = await request.json();
    if (parsed === null || typeof parsed !== "object") {
      return Response.json({ error: "Invalid JSON body" }, { status: 400 });
    }
    body = parsed as {
      phone_number?: unknown;
      first_name?: unknown;
      last_name?: unknown;
      email?: unknown;
    };
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const phoneNumber = typeof body.phone_number === "string"
    ? body.phone_number.trim()
    : "";
  const firstName = typeof body.first_name === "string"
    ? body.first_name.trim()
    : "";
  const lastName = typeof body.last_name === "string" ? body.last_name.trim() : "";
  const email = typeof body.email === "string" ? body.email.trim() : "";
  if (!/^\+[1-9]\d{7,14}$/.test(phoneNumber) || !firstName) {
    return Response.json(
      { error: "phone_number must be E.164 and first_name is required" },
      { status: 422 },
    );
  }

  try {
    const enrollment = await enrollProjectUser({
      phoneNumber,
      firstName,
      lastName: lastName || undefined,
      email: email || undefined,
    });
    return Response.json({
      photon_user_id: enrollment.photonUserId,
      assigned_phone_number: enrollment.assignedPhoneNumber,
    });
  } catch (error) {
    console.error("Could not provision signup messaging:", error);
    return Response.json({ error: "Messaging provisioning failed" }, { status: 502 });
  }
};

Bun.serve({
  port: messagingPort,
  fetch: async (request) => {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/provision-user") {
      return provisionUser(request);
    }
    return new Response("Not found", { status: 404 });
  },
});

console.log(`Photon messaging endpoint listening on port ${messagingPort}`);

const farelockApiUrl = process.env.FARELOCK_API_URL ?? "http://localhost:8000";

const messagingApi = async <T>(path: string, init?: RequestInit): Promise<T> => {
  if (!messagingSecret) throw new Error("PHOTON_INTERNAL_SECRET is required");
  const response = await fetch(new URL(path, farelockApiUrl), {
    ...init,
    headers: {
      "X-Photon-Internal-Secret": messagingSecret,
      ...init?.headers,
    },
  });
  if (!response.ok) throw new Error(`FareLocker messaging API returned HTTP ${response.status}`);
  return response.status === 204 ? undefined as T : await response.json() as T;
};

const welcomeForStart = async (phoneNumber: string): Promise<string | null> => {
  const result = await messagingApi<{ should_send_welcome?: unknown; first_name?: unknown }>(
    `/auth/messaging/start?phone_number=${encodeURIComponent(phoneNumber)}`,
    { method: "POST" },
  );
  return result.should_send_welcome === true && typeof result.first_name === "string"
    ? result.first_name
    : null;
};

const markWelcomeDelivered = (phoneNumber: string) =>
  messagingApi<void>(
    `/auth/messaging/welcome-delivered?phone_number=${encodeURIComponent(phoneNumber)}`,
    { method: "POST" },
  );

// `app.messages` is an async iterable. Each tick yields a `space` (the
// conversation) and an inbound `message`. Reply by awaiting `space.send(...)`.
for await (const [space, message] of app.messages) {
  if (message.direction === "outbound") continue;

  const inboundPhone = phoneFromInbound(space, message);

  if (message.content.type !== "text") continue;
  const query = message.content.text.trim();

  if (query.toUpperCase() === "START") {
    if (!inboundPhone) continue;
    try {
      const firstName = await welcomeForStart(inboundPhone);
      if (!firstName) continue;
      await space.responding(async () => {
        await space.send(welcomeText(firstName));
      });
      await markWelcomeDelivered(inboundPhone);
    } catch (error) {
      console.error("Could not complete START onboarding:", error);
    }
    continue;
  }

  await space.responding(async () => {
    if (query.toUpperCase() === "LOCK") {
      const pending = inboundPhone ? pendingQuotes.get(inboundPhone) : undefined;
      console.info("farelock.lock.received", {
        phone: redactPhone(inboundPhone),
        hasPendingQuote: Boolean(pending),
      });
      if (!inboundPhone || !pending || pending.expiresAt <= Date.now()) {
        if (inboundPhone) pendingQuotes.delete(inboundPhone);
        console.info("farelock.lock.rejected", {
          phone: redactPhone(inboundPhone),
          reason: !pending ? "missing_quote" : "expired_quote",
        });
        await space.send(
          "I don’t have an active FareLock quote. Send your flight details and fare to get one.",
        );
        return;
      }

      try {
        const purchase = await purchaseFareLockFromPhoton(inboundPhone, pending.query);
        pendingQuotes.delete(inboundPhone);
        console.info("farelock.lock.purchased", {
          phone: redactPhone(inboundPhone),
          lockId: purchase.id,
          feeCents: purchase.fee_cents,
          balanceCents: purchase.balance_cents,
        });
        await space.send(formatFareLockPurchase(purchase));
      } catch (error) {
        console.warn("farelock.lock.failed", {
          phone: redactPhone(inboundPhone),
          code: error instanceof Error && "code" in error
            ? (error as { code?: unknown }).code
            : undefined,
          message: error instanceof Error ? error.message : "Unknown error",
        });
        await space.send(formatFareLockError(error));
      }
      return;
    }


try {
  // One conversation per sender, so the agent remembers context.
  const conversationId = inboundPhone ?? String((space as { id?: unknown }).id ?? "unknown");
  const reply = await runAgent(conversationId, query, {
    // The agent prepared a purchase: the user confirms by replying LOCK.
    onFareLockPrepared: (preparedQuery) => {
      if (!inboundPhone) return;
      pendingQuotes.set(inboundPhone, {
        query: preparedQuery,
        expiresAt: Date.now() + quoteTtlMs,
      });
      console.info("farelock.quote.stored", {
        phone: redactPhone(inboundPhone),
        expiresInSeconds: quoteTtlMs / 1000,
      });
    },
  });
  await space.send(reply);
} catch (error) {
  console.error("farelock.agent.failed", error);
  await space.send("Sorry, I hit a snag on my end. Try again in a moment.");
}

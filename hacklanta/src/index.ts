import { attachment, Spectrum, type Space } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";
import {
  fetchFareLockQuote,
  formatFareLockError,
  formatFareLockPurchase,
  formatFareLockQuote,
  purchaseFareLockFromPhoton,
} from "./farelock";

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
const welcomeGifPath = new URL(
  "./public/farelocker-logo-full.gif",
  import.meta.url,
).pathname;

/** Shared Free/Pro lines often reject cold outbound until the user texts first. */
const pendingWelcomes = new Map<string, { firstName: string; queuedAt: number }>();
const pendingQuotes = new Map<string, { query: string; expiresAt: number }>();
const quoteTtlMs = 5 * 60 * 1000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const isTargetNotAllowed = (error: unknown): boolean => {
  const text = error instanceof Error ? error.message : String(error);
  return text.includes("Target not allowed for this project");
};

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
  `Welcome to FareLocker, ${firstName}! Your account is ready.`;

const welcomeAttachment = () =>
  attachment(welcomeGifPath, {
    name: "farelocker-welcome.gif",
    mimeType: "image/gif",
  });

/** Shared-line allowlist: enroll before the first outbound message. */
const enrollProjectUser = async (input: {
  phoneNumber: string;
  firstName: string;
  lastName?: string;
  email?: string;
}): Promise<void> => {
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

  // 409 = already enrolled; still allowed to message.
  if (!projectUser.ok && projectUser.status !== 409) {
    const detail = (await projectUser.text()).slice(0, 300);
    throw new Error(
      `Could not create Photon project user (HTTP ${projectUser.status}): ${detail}`,
    );
  }
};

/** Proactive send (space.create). Fails on shared lines until the user has texted. */
const sendWelcomeProactive = async (
  phoneNumber: string,
  firstName: string,
): Promise<void> => {
  const messaging = imessage(app);
  const user = await messaging.user(phoneNumber);
  const space = await messaging.space.create(user);

  const delaysMs = [0, 500, 1500, 3000];
  let lastError: unknown;
  for (const delayMs of delaysMs) {
    if (delayMs > 0) await sleep(delayMs);
    try {
      await space.send(welcomeText(firstName), welcomeAttachment());
      return;
    } catch (error) {
      lastError = error;
      if (!isTargetNotAllowed(error)) throw error;
    }
  }
  throw lastError;
};

/** Reply-path send into an existing inbound space (works on shared Free/Pro). */
const sendWelcomeInSpace = async (
  space: Pick<Space, "send">,
  firstName: string,
): Promise<void> => {
  await space.send(welcomeText(firstName), welcomeAttachment());
};

const queuePendingWelcome = (phoneNumber: string, firstName: string) => {
  pendingWelcomes.set(phoneNumber, { firstName, queuedAt: Date.now() });
  console.log(
    `Queued welcome for ${phoneNumber} until they text the FareLocker line first`,
  );
};

const flushPendingWelcome = async (
  space: Pick<Space, "send">,
  phoneNumber: string | null,
): Promise<boolean> => {
  if (!phoneNumber) return false;
  const pending = pendingWelcomes.get(phoneNumber);
  if (!pending) return false;
  try {
    await sendWelcomeInSpace(space, pending.firstName);
    pendingWelcomes.delete(phoneNumber);
    console.log(`Delivered queued welcome to ${phoneNumber}`);
    return true;
  } catch (error) {
    console.error(`Failed to deliver queued welcome to ${phoneNumber}:`, error);
    return false;
  }
};

const sendWelcome = async (request: Request): Promise<Response> => {
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
    await enrollProjectUser({
      phoneNumber,
      firstName,
      lastName: lastName || undefined,
      email: email || undefined,
    });
    await sleep(2000);
    await sendWelcomeProactive(phoneNumber, firstName);
    pendingWelcomes.delete(phoneNumber);
    return Response.json({ sent: true, pending: false });
  } catch (error) {
    if (isTargetNotAllowed(error)) {
      // Shared pool: user must text the line once; deliver welcome on first inbound.
      queuePendingWelcome(phoneNumber, firstName);
      return Response.json({
        sent: false,
        pending: true,
        message:
          "User enrolled; welcome will send after they text the FareLocker iMessage line",
      });
    }
    console.error("Could not send signup welcome message:", error);
    return Response.json({ error: "Message delivery failed" }, { status: 502 });
  }
};

Bun.serve({
  port: messagingPort,
  fetch: async (request) => {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/send-welcome") {
      return sendWelcome(request);
    }
    return new Response("Not found", { status: 404 });
  },
});

console.log(`Photon messaging endpoint listening on port ${messagingPort}`);

// `app.messages` is an async iterable. Each tick yields a `space` (the
// conversation) and an inbound `message`. Reply by awaiting `space.send(...)`.
for await (const [space, message] of app.messages) {
  if (message.direction === "outbound") continue;

  const inboundPhone = phoneFromInbound(space, message);
  await flushPendingWelcome(space, inboundPhone);

  if (message.content.type !== "text") continue;
  const query = message.content.text.trim();

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
      const quote = await fetchFareLockQuote(query);
      if (inboundPhone) {
        pendingQuotes.set(inboundPhone, {
          query,
          expiresAt: Date.now() + quoteTtlMs,
        });
        console.info("farelock.quote.stored", {
          phone: redactPhone(inboundPhone),
          expiresInSeconds: quoteTtlMs / 1000,
        });
      } else {
        console.warn("farelock.quote.not_stored", { reason: "sender_phone_unavailable" });
      }
      await space.send(formatFareLockQuote(quote));
    } catch (error) {
      await space.send(formatFareLockError(error));
    }
  });
}

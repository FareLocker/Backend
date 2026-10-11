import { attachment, Spectrum } from "spectrum-ts";
import { imessage } from "@spectrum-ts/imessage";
import {
  fetchFareLockQuote,
  formatFareLockError,
  formatFareLockQuote,
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

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const isTargetNotAllowed = (error: unknown): boolean => {
  const text = error instanceof Error ? error.message : String(error);
  return text.includes("Target not allowed for this project");
};

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

const sendWelcomeMessage = async (
  phoneNumber: string,
  firstName: string,
): Promise<void> => {
  const messaging = imessage(app);
  const user = await messaging.user(phoneNumber);
  const space = await messaging.space.create(user);

  // Allowlist can lag briefly after enroll; retry Target-not-allowed a few times.
  const delaysMs = [0, 500, 1500, 3000];
  let lastError: unknown;
  for (const delayMs of delaysMs) {
    if (delayMs > 0) await sleep(delayMs);
    try {
      await space.send(
        `Welcome to FareLocker, ${firstName}! Your account is ready.`,
        attachment(welcomeGifPath, {
          name: "farelocker-welcome.gif",
          mimeType: "image/gif",
        }),
      );
      return;
    } catch (error) {
      lastError = error;
      if (!isTargetNotAllowed(error)) throw error;
    }
  }
  throw lastError;
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
    // 1) Allowlist on the Photon project, then 2) send the welcome iMessage.
    await enrollProjectUser({
      phoneNumber,
      firstName,
      lastName: lastName || undefined,
      email: email || undefined,
    });
    // Shared-pool allowlist can lag right after create; wait before first send.
    await sleep(2000);
    await sendWelcomeMessage(phoneNumber, firstName);
    return Response.json({ sent: true });
  } catch (error) {
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
  if (message.content.type !== "text") continue;
  const query = message.content.text;

  await space.responding(async () => {
    try {
      const quote = await fetchFareLockQuote(query);
      await space.send(formatFareLockQuote(quote));
    } catch (error) {
      await space.send(formatFareLockError(error));
    }
  });
}

import { Spectrum } from "spectrum-ts";
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

const messagingSecret = process.env.PHOTON_INTERNAL_SECRET;
const messagingPort = Number(process.env.PHOTON_MESSAGING_PORT ?? 8787);

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

  let body: { phone_number?: unknown; first_name?: unknown };
  try {
    const parsed: unknown = await request.json();
    if (parsed === null || typeof parsed !== "object") {
      return Response.json({ error: "Invalid JSON body" }, { status: 400 });
    }
    body = parsed as { phone_number?: unknown; first_name?: unknown };
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const phoneNumber = typeof body.phone_number === "string"
    ? body.phone_number.trim()
    : "";
  const firstName = typeof body.first_name === "string"
    ? body.first_name.trim()
    : "";
  if (!/^\+[1-9]\d{7,14}$/.test(phoneNumber) || !firstName) {
    return Response.json(
      { error: "phone_number must be E.164 and first_name is required" },
      { status: 422 },
    );
  }

  try {
    const messaging = imessage(app);
    const user = await messaging.user(phoneNumber);
    const space = await messaging.space.create(user);
    await space.send(`Welcome to FareLocker, ${firstName}! Your account is ready.`);
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

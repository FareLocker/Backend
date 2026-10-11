import express from "express";
import { IMessageSDK } from "@photon-ai/imessage-kit";
import { runAgent } from "./agent";

const sdk = new IMessageSDK({
  webhook: {
    url: `${process.env.PUBLIC_URL}/photon/webhook`,
    headers: { Authorization: `Bearer ${process.env.WEBHOOK_SECRET}` },
  },
});

const app = express();
app.use(express.json());

app.post("/gemini/chat", async (req, res) => {
  const { chatId = "test", text } = req.body;
  if (!text) return res.status(400).json({ error: "text required" });
  try {
    res.json({ reply: await runAgent(chatId, text) });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: "gemini failed" });
  }
});

app.post("/photon/webhook", async (req, res) => {
  if (req.headers.authorization !== `Bearer ${process.env.WEBHOOK_SECRET}`) {
    return res.sendStatus(401);
  }
  res.sendStatus(200);

  const { message } = req.body;
  if (!message?.text || message.isFromMe) return;

  try {
    const reply = await runAgent(message.chatId ?? message.sender, message.text);
    await sdk.send({ to: message.sender, text: reply });
  } catch (e) {
    console.error(e);
  }
});

app.listen(3000, async () => {
  await sdk.startWatching();
  console.log("listening on :3000");
});
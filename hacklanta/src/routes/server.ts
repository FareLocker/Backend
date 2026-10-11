import express from "express";
import { GoogleGenAI } from "@google/genai";
import { IMessageSDK } from "@photon-ai/imessage-kit";


const MODEL = process.env.GEMINI_MODEL
const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API });

const sdk = new IMessageSDK({
  webhook: {
    url: process.env.PUBLIC_URL + "photon/webook",
    headers: {
      "Authorization":
        `Bearer ${process.env.WEBHOOK_SECRET} `
    }
  }
});

const history = new Map < string, { role: "user" | "model"; parts: { text: string } "}


async function askGemini(chatId: string, text: string) {
  const h = history.get(chatId) ?? [];
  h.push({ role: "user", parts: [{ text }] });
}

const res = await ai.models.generateContent({
  model: MODEL,
  contents: h,
  config: { systemIntructions: "Keep replies short and be friendly" },
})

const reply = res.text ?? "";
h.push({ role: "model", parts: [{ text: reply }] });
history.set(chatID, h.slice(-20));
return reply;
}

const app = express();
app.use(express.json());

app.post("/gemini/chat", async (req, res) => {
  const { chatId = "test", text } = req.body;
  if (!text) return res.status(400).json({ error: "text required" });
  try {
    res.json({ reply: await askGemini(chatId, text) });
  } catch (e)
})
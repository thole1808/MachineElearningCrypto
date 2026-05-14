const chat = document.querySelector("#chat");
const form = document.querySelector("#form");
const message = document.querySelector("#message");
const send = document.querySelector("#send");
const statusBadge = document.querySelector("#status");

function addBubble(role, text) {
  const bubble = document.createElement("article");
  bubble.className = `bubble ${role}`;
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  bubble.appendChild(paragraph);
  chat.appendChild(bubble);
  chat.scrollTop = chat.scrollHeight;
  return bubble;
}

async function sendMessage(text) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: text }),
  });

  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.error || "Gagal mengirim pesan");
  }
  return body.reply;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = message.value.trim();
  if (!text) return;

  message.value = "";
  addBubble("user", text);
  const pending = addBubble("assistant", "Menjawab...");
  send.disabled = true;
  statusBadge.textContent = "Thinking";

  try {
    pending.querySelector("p").textContent = await sendMessage(text);
  } catch (error) {
    pending.querySelector("p").textContent = error.message;
  } finally {
    send.disabled = false;
    statusBadge.textContent = "Local";
    message.focus();
    chat.scrollTop = chat.scrollHeight;
  }
});

message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

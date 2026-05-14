const chat = document.querySelector("#chat");
const form = document.querySelector("#form");
const message = document.querySelector("#message");
const send = document.querySelector("#send");
const statusBadge = document.querySelector("#status");
const binanceCheck = document.querySelector("#binance-check");

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

async function checkBinance() {
  const response = await fetch("/api/binance/status");
  const body = await response.json();
  if (!response.ok || !body.ok) {
    throw new Error(body.error || "Gagal cek Binance");
  }
  return body.binance;
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

binanceCheck.addEventListener("click", async () => {
  const pending = addBubble("assistant", "Mengecek koneksi Binance...");
  binanceCheck.disabled = true;
  statusBadge.textContent = "Binance";

  try {
    const binance = await checkBinance();
    const price = binance.price?.price || "-";
    const balances = binance.balances?.length
      ? binance.balances
          .map((item) => `${item.asset}: free ${item.free}, locked ${item.locked}`)
          .join("\n")
      : "API key belum diisi atau saldo testnet kosong.";

    pending.querySelector("p").textContent = [
      `Mode: ${binance.mode}`,
      `Symbol: ${binance.symbol}`,
      `Base URL: ${binance.base_url}`,
      `Harga: ${price}`,
      `API key: ${binance.has_keys ? "terpasang" : "belum diisi"}`,
      "",
      balances,
    ].join("\n");
  } catch (error) {
    pending.querySelector("p").textContent = error.message;
  } finally {
    binanceCheck.disabled = false;
    statusBadge.textContent = "Local";
  }
});

"use strict";

(() => {
  const MAX_HISTORY_TURNS = 12;
  const MAX_HISTORY_TURN_CHARS = 2000;
  const SLOW_NOTICE_MS = 20_000;
  const REQUEST_TIMEOUT_MS = 180_000;
  const AGENT_JOIN_TIMEOUT_MS = 15_000;
  const INTERNAL_PATH = /^\/(?:cursos(?:\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?|registro|login|mis-cursos)$/i;
  const CHIPS = ["Quiero aprender marketing digital", "Cursos de IA", "Como me inscribo"];

  // Enlaces markdown, negritas, URL sueltas y rutas internas, en ese orden de prioridad.
  const INLINE =
    /\[([^\]\n]+)\]\(([^)\s]+)\)|\*\*([^*\n]+)\*\*|(https?:\/\/[^\s<>"')\]]+)|(?<![\w/.-])(\/(?:cursos(?:\/[0-9a-f-]{36})?|registro|login|mis-cursos))(?![\w/-])/gi;

  const $ = (id) => document.getElementById(id);
  const el = {
    status: $("status"),
    modeText: $("mode-text"),
    modeVoice: $("mode-voice"),
    textView: $("text-view"),
    voiceView: $("voice-view"),
    messages: $("messages"),
    chips: $("chips"),
    composer: $("composer"),
    draft: $("draft"),
    send: $("send"),
    voiceStatus: $("voice-status"),
    voiceHint: $("voice-hint"),
    mic: $("mic"),
    caption: $("caption"),
    captionLabel: $("caption-label"),
    backToText: $("back-to-text"),
    audioSink: $("audio-sink"),
  };

  const state = {
    config: { platformUrl: "", embedOrigins: [], voiceEnabled: false },
    parentOrigin: null,
    turns: [], // { role: "user" | "assistant", content }
    busy: false,
    retry: null,
    mode: "text",
  };

  const configReady = fetch("/config")
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((config) => {
      state.config = config;
    })
    .catch(() => setOnline(false));

  function setOnline(online) {
    el.status.textContent = online ? "En linea" : "Sin conexion";
    el.status.className = `badge ${online ? "badge--success" : "badge--danger"}`;
  }

  function scrollToEnd() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  /* ---------- Enlaces y renderizado seguro ---------- */

  function internalPath(url) {
    if (url.startsWith("/")) return INTERNAL_PATH.test(url) ? url : null;
    try {
      const parsed = new URL(url);
      const known = [state.config.platformUrl, ...state.config.embedOrigins].some((o) => {
        try {
          return new URL(o).origin === parsed.origin;
        } catch {
          return false;
        }
      });
      return known && INTERNAL_PATH.test(parsed.pathname) ? parsed.pathname : null;
    } catch {
      return null;
    }
  }

  function linkNode(label, path) {
    const a = document.createElement("a");
    a.textContent = label;
    a.href = new URL(path, state.config.platformUrl || location.origin).href;
    // Sin plataforma alrededor se abre aparte; embebido, la plataforma navega sin recargar.
    a.target = "_blank";
    a.rel = "noopener";
    a.addEventListener("click", (event) => {
      if (!state.parentOrigin) return;
      event.preventDefault();
      window.parent.postMessage({ type: "edy:navigate", path }, state.parentOrigin);
    });
    return a;
  }

  // Todo se crea con nodos de texto: el contenido nunca se interpreta como HTML.
  function appendFormatted(container, text) {
    let last = 0;
    for (const match of text.matchAll(INLINE)) {
      const [raw, mdLabel, mdUrl, bold, bareUrl, route] = match;
      let node = null;
      let trailing = "";
      if (mdUrl) {
        const path = internalPath(mdUrl);
        if (path) node = linkNode(mdLabel, path);
      } else if (bold) {
        node = document.createElement("strong");
        node.textContent = bold;
      } else if (bareUrl) {
        const url = bareUrl.replace(/[.,;:!?]+$/, "");
        trailing = bareUrl.slice(url.length);
        const path = internalPath(url);
        if (path) node = linkNode(url, path);
      } else if (route) {
        const path = internalPath(route);
        if (path) node = linkNode(route, path);
      }
      if (!node) continue; // Se queda como texto y entra en el siguiente tramo.
      container.append(text.slice(last, match.index), node, trailing);
      last = match.index + raw.length;
    }
    container.append(text.slice(last));
  }

  function appendBubble(role, content) {
    const bubble = document.createElement("div");
    bubble.className = `msg${role === "user" ? " msg--user" : ""}`;
    if (role === "user") bubble.textContent = content;
    else appendFormatted(bubble, content);
    el.messages.append(bubble);
    scrollToEnd();
  }

  function addTurn(role, content) {
    state.turns.push({ role, content });
    appendBubble(role, content);
  }

  /* ---------- Conversacion de texto ---------- */

  function historyBefore(index) {
    // El servidor rechaza turnos vacios o largos: se recortan aqui en lugar de romper la conversacion.
    return state.turns
      .slice(0, index)
      .map((t) => ({ role: t.role, content: t.content.trim().slice(0, MAX_HISTORY_TURN_CHARS) }))
      .filter((t) => t.content)
      .slice(-MAX_HISTORY_TURNS);
  }

  function setBusy(busy) {
    state.busy = busy;
    el.send.disabled = busy;
    for (const chip of el.chips.children) chip.disabled = busy;
  }

  function clearRetry() {
    state.retry?.remove();
    state.retry = null;
  }

  function showError(message, onRetry) {
    clearRetry();
    const box = document.createElement("div");
    box.className = "error";
    box.setAttribute("role", "alert");
    box.append(message);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn--retry";
    button.textContent = "Reintentar";
    button.addEventListener("click", onRetry);
    box.append(button);
    el.messages.append(box);
    state.retry = box;
    scrollToEnd();
    return button;
  }

  async function request(message, turnIndex) {
    clearRetry();
    setBusy(true);
    const typing = document.createElement("div");
    typing.className = "typing";
    typing.setAttribute("role", "status");
    typing.setAttribute("aria-label", "Edy esta escribiendo");
    typing.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
    el.messages.append(typing);
    scrollToEnd();

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    let notice = null;
    const slow = setTimeout(() => {
      notice = document.createElement("p");
      notice.className = "notice";
      notice.setAttribute("role", "status");
      notice.textContent = "Sigo buscando, esto esta tardando mas de lo normal";
      el.messages.append(notice);
      scrollToEnd();
    }, SLOW_NOTICE_MS);

    let reply = null;
    let failure = null;
    try {
      const response = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history: historyBefore(turnIndex) }),
        signal: controller.signal,
      });
      if (response.status === 429) failure = { text: "Demasiados mensajes, intenta en un momento", offline: false };
      else if (!response.ok) failure = { text: "No pude responder ahora.", offline: true };
      else reply = (await response.json()).reply || "No pude completar la respuesta.";
    } catch {
      failure = { text: "No pude responder ahora.", offline: true };
    }

    clearTimeout(timeout);
    clearTimeout(slow);
    typing.remove();
    notice?.remove();
    setBusy(false);

    if (failure) {
      setOnline(!failure.offline);
      showError(failure.text, () => request(message, turnIndex));
      return;
    }
    setOnline(true);
    addTurn("assistant", reply);
  }

  function send(text) {
    const message = text.trim();
    if (!message || state.busy) return false;
    clearRetry();
    addTurn("user", message);
    request(message, state.turns.length - 1);
    return true;
  }

  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    if (send(el.draft.value)) el.draft.value = "";
  });

  for (const label of CHIPS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = label;
    chip.addEventListener("click", () => send(label));
    el.chips.append(chip);
  }

  /* ---------- Voz ---------- */

  const VOICE_TEXT = {
    idle: "Toca el microfono para hablar",
    connecting: "Conectando...",
    listening: "Escuchando...",
    thinking: "Pensando...",
    speaking: "Respondiendo...",
  };

  const voice = {
    room: null,
    phase: "idle",
    run: 0, // Se incrementa al terminar: invalida lo que siga pendiente de una sesion anterior.
    agentTimer: null,
    segments: new Map(), // id -> { role, text }; conserva el orden de llegada.
  };

  function renderVoice(message, isError = false) {
    const active = voice.phase !== "idle";
    el.voiceView.dataset.voice = voice.phase;
    el.voiceStatus.textContent = message ?? VOICE_TEXT[voice.phase];
    el.voiceStatus.classList.toggle("voice__status--error", isError);
    el.voiceHint.textContent = active ? "Toca para detener" : "Toca para iniciar";
    el.mic.setAttribute("aria-label", active ? "Detener conversacion de voz" : "Iniciar conversacion de voz");
    el.mic.setAttribute("aria-pressed", String(active));
  }

  function showCaption(role, text) {
    el.captionLabel.textContent = role === "assistant" ? "Asistente" : "Transcripcion";
    el.caption.textContent = text;
  }

  function flushTranscript() {
    for (const { role, text } of voice.segments.values()) {
      if (text.trim()) addTurn(role, text.trim());
    }
    voice.segments.clear();
  }

  function endVoice(message, isError = false) {
    if (voice.phase === "idle" && !voice.room) {
      if (message) renderVoice(message, isError);
      return;
    }
    voice.run += 1;
    clearTimeout(voice.agentTimer);
    const room = voice.room;
    voice.room = null;
    voice.phase = "idle";
    if (room) room.disconnect();
    el.audioSink.replaceChildren();
    flushTranscript();
    showCaption("user", "Tu conversacion aparecera aqui.");
    renderVoice(message, isError);
  }

  function applyAgentState(agentState) {
    if (voice.phase === "idle") return;
    if (agentState === "thinking" || agentState === "speaking" || agentState === "listening") {
      voice.phase = agentState;
      renderVoice();
    }
  }

  function agentJoined(participant) {
    clearTimeout(voice.agentTimer);
    applyAgentState(participant.attributes?.["lk.agent.state"]);
  }

  async function readTranscript(reader, sender, run) {
    if (run !== voice.run) return;
    const id = reader.info.attributes?.["lk.segment_id"] ?? reader.info.id;
    const role = sender.identity === voice.room?.localParticipant.identity ? "user" : "assistant";
    // Edy envia una corriente con fragmentos; cada parcial de la persona es una corriente completa.
    // Por eso se acumula por corriente y la mas reciente reemplaza al segmento.
    let text = "";
    for await (const chunk of reader) {
      if (run !== voice.run) return;
      text += chunk;
      voice.segments.set(id, { role, text });
      showCaption(role, text);
    }
  }

  async function startVoice() {
    const run = voice.run;
    const lk = window.LivekitClient;
    if (!state.config.voiceEnabled || !lk) {
      renderVoice("La voz no esta disponible ahora", true);
      return;
    }
    voice.phase = "connecting";
    renderVoice();

    // El permiso se pide antes del token: un permiso denegado no debe gastar una sesion.
    try {
      const probe = await navigator.mediaDevices.getUserMedia({ audio: true });
      probe.getTracks().forEach((track) => track.stop());
    } catch {
      if (run === voice.run) endVoice("Necesito permiso de microfono para hablar contigo", true);
      return;
    }
    if (run !== voice.run) return;

    let session;
    try {
      const response = await fetch("/voice/token", { method: "POST" });
      if (response.status === 429) {
        if (run === voice.run) endVoice("Demasiadas sesiones de voz, intenta mas tarde", true);
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      session = await response.json();
    } catch {
      if (run === voice.run) endVoice("La voz no esta disponible ahora", true);
      return;
    }
    if (run !== voice.run) return;

    const room = new lk.Room();
    voice.room = room;
    const { RoomEvent } = lk;
    const unavailable = () => {
      if (run === voice.run) endVoice("La voz no esta disponible ahora", true);
    };
    const finished = () => {
      if (run === voice.run) endVoice("La sesion de voz termino");
    };

    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === "audio") el.audioSink.append(track.attach());
    });
    room.on(RoomEvent.TrackUnsubscribed, (track) => {
      track.detach().forEach((node) => node.remove());
    });
    room.on(RoomEvent.ParticipantConnected, (participant) => {
      if (participant.isAgent && run === voice.run) agentJoined(participant);
    });
    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
      if (participant.isAgent) finished();
    });
    room.on(RoomEvent.ParticipantAttributesChanged, (_changed, participant) => {
      if (participant.isAgent && run === voice.run) applyAgentState(participant.attributes["lk.agent.state"]);
    });
    room.on(RoomEvent.Disconnected, finished);
    room.registerTextStreamHandler("lk.transcription", (reader, sender) => {
      readTranscript(reader, sender, run).catch(() => {});
    });

    voice.agentTimer = setTimeout(unavailable, AGENT_JOIN_TIMEOUT_MS);
    try {
      await room.connect(session.url, session.token);
      if (run !== voice.run) return;
      await room.startAudio();
      await room.localParticipant.setMicrophoneEnabled(true);
      if (run !== voice.run) return;
      for (const participant of room.remoteParticipants.values()) {
        if (participant.isAgent) agentJoined(participant);
      }
    } catch (error) {
      if (run !== voice.run) return;
      if (error?.name === "NotAllowedError" || error?.name === "NotFoundError") {
        endVoice("Necesito permiso de microfono para hablar contigo", true);
      } else {
        unavailable();
      }
    }
  }

  el.mic.addEventListener("click", () => {
    if (voice.phase === "idle") startVoice();
    else endVoice();
  });

  /* ---------- Modos ---------- */

  function setMode(mode) {
    state.mode = mode;
    const isVoice = mode === "voice";
    if (!isVoice) endVoice();
    el.textView.hidden = isVoice;
    el.voiceView.hidden = !isVoice;
    el.modeText.setAttribute("aria-pressed", String(!isVoice));
    el.modeVoice.setAttribute("aria-pressed", String(isVoice));
    if (!isVoice) scrollToEnd();
  }

  function focusActive() {
    (state.mode === "voice" ? el.mic : el.draft).focus();
  }

  el.modeText.addEventListener("click", () => setMode("text"));
  el.modeVoice.addEventListener("click", () => setMode("voice"));
  el.backToText.addEventListener("click", () => {
    setMode("text");
    focusActive();
  });

  /* ---------- Puente con la plataforma ---------- */

  window.addEventListener("message", async (event) => {
    if (event.source !== window.parent || event.source === window) return;
    const type = event.data?.type;
    if (type === "edy:init") {
      await configReady;
      if (!state.config.embedOrigins.includes(event.origin)) return;
      state.parentOrigin = event.origin;
      window.parent.postMessage({ type: "edy:ready" }, event.origin);
      return;
    }
    if (event.origin !== state.parentOrigin) return;
    if (type === "edy:focus") focusActive();
    else if (type === "edy:hidden") endVoice();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.parentOrigin) {
      window.parent.postMessage({ type: "edy:close" }, state.parentOrigin);
    }
  });

  window.addEventListener("pagehide", () => endVoice());

  /* ---------- Inicio ---------- */

  renderVoice();
  appendBubble("assistant", "Hola, soy Edy, el asistente de EduTech. ¿Que quieres aprender?");
})();

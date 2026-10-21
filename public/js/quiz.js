/* Quiz System — attempt timer and answer autosave */
(function () {
  "use strict";

  const root = document.getElementById("attempt-root");
  if (!root) return;

  const form = document.querySelector("[data-attempt-form]");
  const attemptId = root.dataset.attemptId;
  const csrf = root.dataset.csrf;
  const saveUrl = root.dataset.saveUrl;
  const stateUrl = root.dataset.stateUrl;
  const total = parseInt(root.dataset.total || "0", 10);

  const saveState = document.querySelector("[data-save-state]");
  const saveLabel = document.querySelector("[data-save-label]");
  const timerEl = document.querySelector("[data-timer]");
  const timerBar = document.querySelector("[data-timer-bar]");
  const timerWrap = document.querySelector("[data-timer-wrap]");
  const answeredEls = document.querySelectorAll("[data-answered]");
  const submitButtons = document.querySelectorAll("[data-submit-button]");

  const dirty = new Set();
  let saving = false;
  let saveTimer = null;
  let stateTimer = null;
  let deadlineMs = null;
  let totalSeconds = null;
  let autoSubmitted = false;
  let finished = false;

  // ---------------------------------------------------------------------
  // Answer state
  // ---------------------------------------------------------------------

  function cardFor(questionId) {
    return root.querySelector('[data-question-card][data-question-id="' + questionId + '"]');
  }

  function answerFor(questionId) {
    const card = cardFor(questionId);
    if (!card) return null;
    if (card.dataset.questionType === "mcq") {
      const checked = card.querySelector('input[type="radio"]:checked');
      return checked ? { question_id: Number(questionId), option_id: Number(checked.value) } : null;
    }
    const area = card.querySelector("textarea");
    return area ? { question_id: Number(questionId), text: area.value } : null;
  }

  function isAnswered(questionId) {
    const card = cardFor(questionId);
    if (!card) return false;
    if (card.dataset.questionType === "mcq") {
      return !!card.querySelector('input[type="radio"]:checked');
    }
    const area = card.querySelector("textarea");
    return !!(area && area.value.trim());
  }

  function refreshProgress() {
    let count = 0;
    root.querySelectorAll("[data-question-card]").forEach(function (card) {
      const id = card.dataset.questionId;
      const answered = isAnswered(id);
      if (answered) count += 1;
      const pip = root.querySelector('[data-pip="' + id + '"]');
      if (pip) pip.classList.toggle("answered", answered);
      card.classList.toggle("answered", answered);
    });
    answeredEls.forEach(function (el) { el.textContent = String(count); });
  }

  function setSaveState(kind, text) {
    if (!saveState) return;
    saveState.classList.remove("saving", "error");
    if (kind === "saving") saveState.classList.add("saving");
    if (kind === "error") saveState.classList.add("error");
    if (saveLabel) saveLabel.textContent = text;
  }

  // ---------------------------------------------------------------------
  // Autosave
  // ---------------------------------------------------------------------

  function markDirty(questionId) {
    dirty.add(String(questionId));
    refreshProgress();
    setSaveState("saving", "Unsaved changes");
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(flush, 800);
  }

  async function saveQuestion(questionId) {
    const payload = answerFor(questionId);
    if (!payload) return true;
    try {
      const response = await fetch(saveUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
          "Accept": "application/json"
        },
        body: JSON.stringify(payload),
        credentials: "same-origin"
      });

      if (response.status === 409) {
        const data = await response.json().catch(function () { return {}; });
        if (data.redirect) {
          finished = true;
          window.location.href = data.redirect;
        }
        return false;
      }
      if (!response.ok) throw new Error("Save failed");
      return true;
    } catch (err) {
      return false;
    }
  }

  async function flush() {
    if (saving || finished) return true;
    if (dirty.size === 0) {
      setSaveState("ok", "All changes saved");
      return true;
    }
    saving = true;
    setSaveState("saving", "Saving…");
    const ids = Array.from(dirty);
    let ok = true;
    for (const id of ids) {
      const saved = await saveQuestion(id);
      if (saved) dirty.delete(id);
      else ok = false;
    }
    saving = false;
    if (ok) {
      setSaveState("ok", dirty.size ? "Saving…" : "All changes saved");
    } else {
      setSaveState("error", "Save failed — will retry");
    }
    return ok;
  }

  // React to edits
  root.addEventListener("input", function (event) {
    const target = event.target;
    const card = target.closest("[data-question-card]");
    if (!card) return;
    if (target.matches("textarea")) {
      markDirty(card.dataset.questionId);
      const counter = card.querySelector("[data-char-count]");
      if (counter) counter.textContent = target.value.length + " characters";
    }
  });

  root.addEventListener("change", function (event) {
    const target = event.target;
    if (!target.matches('input[type="radio"]')) return;
    const card = target.closest("[data-question-card]");
    if (!card) return;
    root.querySelectorAll('[data-question-card][data-question-id="' + card.dataset.questionId + '"] .option-row').forEach(function (row) {
      row.classList.toggle("selected", !!row.querySelector("input:checked"));
    });
    markDirty(card.dataset.questionId);
  });

  // Periodic flush + retry
  window.setInterval(function () {
    if (dirty.size) flush();
  }, 9000);

  // ---------------------------------------------------------------------
  // Timer
  // ---------------------------------------------------------------------

  function renderTimer(seconds) {
    if (!timerEl) return;
    const clamped = Math.max(0, Math.floor(seconds));
    const mins = Math.floor(clamped / 60);
    const secs = clamped % 60;
    timerEl.textContent = String(mins).padStart(2, "0") + ":" + String(secs).padStart(2, "0");
    if (timerWrap) timerWrap.classList.toggle("urgent", clamped <= 60);
    if (timerBar && totalSeconds) {
      const pct = Math.max(0, Math.min(100, (clamped / totalSeconds) * 100));
      timerBar.style.width = pct.toFixed(2) + "%";
    }
    document.title = "(" + timerEl.textContent + ") " + document.title.replace(/^\([^)]*\)\s*/, "");
  }

  function tick() {
    if (finished) return;
    if (deadlineMs === null) return;
    const remaining = (deadlineMs - Date.now()) / 1000;
    renderTimer(remaining);
    if (remaining <= 0) {
      autoSubmit();
    }
  }

  async function autoSubmit() {
    if (autoSubmitted || finished) return;
    autoSubmitted = true;
    finished = true;
    setSaveState("saving", "Time's up — submitting…");
    await flush();
    if (!form) return;
    form.dataset.autoSubmit = "1";
    form.submit();
  }

  function startTimer(remaining) {
    if (remaining === null || remaining === undefined) return;
    totalSeconds = Math.max(1, remaining);
    deadlineMs = Date.now() + remaining * 1000;
    renderTimer(remaining);
    tick();
    window.clearInterval(window.__quizTick);
    window.__quizTick = window.setInterval(tick, 1000);
  }

  async function syncState() {
    if (finished) return;
    try {
      const response = await fetch(stateUrl, { headers: { "Accept": "application/json" }, credentials: "same-origin" });
      if (!response.ok) return;
      const data = await response.json();
      if (data.redirect) {
        finished = true;
        window.location.href = data.redirect;
        return;
      }
      if (data.status === "in_progress" && typeof data.remaining === "number") {
        startTimer(data.remaining);
      }
    } catch (err) {
      /* offline: the local clock keeps running and the server still enforces the deadline */
    }
  }

  // ---------------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------------

  if (form) {
    form.addEventListener("submit", async function (event) {
      if (form.dataset.autoSubmit === "1") {
        return; // native submit after autosave
      }
      event.preventDefault();
      if (finished) return;

      let unanswered = 0;
      root.querySelectorAll("[data-question-card]").forEach(function (card) {
        if (!isAnswered(card.dataset.questionId)) unanswered += 1;
      });

      const message = unanswered > 0
        ? "You still have " + unanswered + " unanswered question" + (unanswered === 1 ? "" : "s") + ". Submit anyway?"
        : "Submit your answers? You won't be able to change them afterwards.";
      if (!window.confirm(message)) return;

      submitButtons.forEach(function (button) {
        button.disabled = true;
        button.dataset.originalText = button.textContent;
        button.textContent = "Submitting…";
      });

      const ok = await flush();
      if (!ok) {
        const proceed = window.confirm("Some answers could not be saved. Submit anyway? Your latest edits may be missing.");
        if (!proceed) {
          submitButtons.forEach(function (button) {
            button.disabled = false;
            if (button.dataset.originalText) button.textContent = button.dataset.originalText;
          });
          return;
        }
      }

      finished = true;
      form.dataset.autoSubmit = "1";
      form.submit();
    });
  }

  // Navigation warning for unsaved work
  window.addEventListener("beforeunload", function (event) {
    if (dirty.size > 0 && !finished) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  // Highlight the question currently in view
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        const id = entry.target.dataset.questionId;
        root.querySelectorAll("[data-pip]").forEach(function (pip) {
          pip.classList.toggle("current", pip.dataset.pip === id);
        });
      });
    }, { rootMargin: "-160px 0px -55% 0px", threshold: 0.1 });
    root.querySelectorAll("[data-question-card]").forEach(function (card) { observer.observe(card); });
  }

  // Boot
  refreshProgress();
  if (timerEl) {
    syncState().then(function () {
      stateTimer = window.setInterval(syncState, 60000);
    });
  }
})();

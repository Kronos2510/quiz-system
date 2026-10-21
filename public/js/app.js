/* Quiz System — shared interface behaviour */
(function () {
  "use strict";

  // Mobile navigation -------------------------------------------------------
  const navToggle = document.querySelector("[data-nav-toggle]");
  const nav = document.querySelector("[data-nav]");
  if (navToggle && nav) {
    navToggle.addEventListener("click", function () {
      const open = nav.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // Flash messages ----------------------------------------------------------
  document.querySelectorAll("[data-flash]").forEach(function (flash) {
    const close = flash.querySelector("[data-flash-close]");
    if (close) {
      close.addEventListener("click", function () { flash.remove(); });
    }
    window.setTimeout(function () {
      flash.style.transition = "opacity .3s ease, transform .3s ease";
      flash.style.opacity = "0";
      flash.style.transform = "translateY(-4px)";
      window.setTimeout(function () { flash.remove(); }, 320);
    }, 7000);
  });

  // Confirmation prompts for destructive forms ------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.confirmed === "1") return;
      const message = form.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    });
  });

  // Copy-to-clipboard -------------------------------------------------------
  function fallbackCopy(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    document.body.removeChild(area);
    return ok;
  }

  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", async function () {
      const text = button.getAttribute("data-copy") || "";
      if (!text) return;
      let ok = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
          ok = true;
        }
      } catch (err) { ok = false; }
      if (!ok) ok = fallbackCopy(text);

      const previous = button.getAttribute("aria-label");
      button.setAttribute("aria-label", ok ? "Copied" : "Copy failed");
      button.classList.add(ok ? "copied" : "copy-failed");
      window.setTimeout(function () {
        button.classList.remove("copied", "copy-failed");
        if (previous) button.setAttribute("aria-label", previous);
      }, 1400);
    });
  });

  // Role cards in registration ---------------------------------------------
  document.querySelectorAll(".role-option input").forEach(function (input) {
    input.addEventListener("change", function () {
      document.querySelectorAll(".role-option").forEach(function (option) {
        option.classList.toggle("selected", !!option.querySelector("input:checked"));
      });
    });
  });

  // Quiz question option editor --------------------------------------------
  const optionList = document.querySelector("[data-option-rows]");
  const addOption = document.querySelector("[data-add-option]");

  function reindexOptions() {
    if (!optionList) return;
    optionList.querySelectorAll("[data-option-row]").forEach(function (row, index) {
      const radio = row.querySelector('input[type="radio"]');
      const text = row.querySelector('input[type="text"]');
      if (radio) radio.value = String(index);
      if (text) text.placeholder = "Option " + (index + 1);
    });
  }

  if (optionList && addOption) {
    addOption.addEventListener("click", function () {
      const rows = optionList.querySelectorAll("[data-option-row]");
      const last = rows[rows.length - 1];
      if (!last) return;
      const clone = last.cloneNode(true);
      const radio = clone.querySelector('input[type="radio"]');
      const text = clone.querySelector('input[type="text"]');
      if (radio) radio.checked = false;
      if (text) text.value = "";
      optionList.appendChild(clone);
      reindexOptions();
      if (text) text.focus();
    });

    optionList.addEventListener("click", function (event) {
      const remove = event.target.closest("[data-remove-option]");
      if (!remove) return;
      const rows = optionList.querySelectorAll("[data-option-row]");
      if (rows.length <= 2) {
        window.alert("A multiple-choice question needs at least two options.");
        return;
      }
      const row = remove.closest("[data-option-row]");
      if (row) row.remove();
      reindexOptions();
    });
  }
})();

/* ============================================================
   Loan Application Guided Tour (adaptive coachmark)
   Spans: Quick Loan -> Loan Apply -> Payment Method -> Under review
   - Dims the page + spotlights one field at a time
   - User fills the highlighted field directly
   - Auto-advances when the field is completed (smart detection)
   - Skip / Back / Next controls; remembers progress across pages
   English UI only. Style/behaviour additive - never blocks the form.
   ============================================================ */
(function () {
  "use strict";

  var SEEN_KEY = "loanTourSeen_v1";   // set once finished or skipped
  var ACTIVE_KEY = "loanJourney_v1";  // "1" while the guided journey is running

  function lsGet(k){ try { return localStorage.getItem(k); } catch(e){ return null; } }
  function lsSet(k,v){ try { localStorage.setItem(k,v); } catch(e){} }
  function lsDel(k){ try { localStorage.removeItem(k); } catch(e){} }

  // ---- inject styles once ----
  function injectStyles(){
    if (document.getElementById("loanTourStyles")) return;
    var css = ''
      + '.lt-spot{position:fixed;top:0;left:0;width:0;height:0;border-radius:22px;'
      + 'box-shadow:0 0 0 9999px rgba(40,16,22,.52);transition:top .3s cubic-bezier(.22,.61,.36,1),left .3s cubic-bezier(.22,.61,.36,1),width .3s ease,height .3s ease;'
      + 'pointer-events:none;z-index:2147482000;}'
      + '.lt-spot.lt-hidden{display:none;}'

      + '.lt-tip{position:fixed;top:50%;left:50%;width:min(330px,90vw);border-radius:26px;'
      + 'padding:20px 20px 18px;z-index:2147483000;'
      + 'background:linear-gradient(180deg,rgba(255,255,255,.96) 0%,rgba(250,238,237,.94) 100%);'
      + '-webkit-backdrop-filter:blur(18px) saturate(150%);backdrop-filter:blur(18px) saturate(150%);'
      + 'box-shadow:0 22px 46px rgba(90,40,50,.30),0 3px 8px rgba(90,40,50,.10),inset 0 2px 3px rgba(255,255,255,.95);'
      + "font-family:'Outfit',system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;"
      + 'transition:top .3s cubic-bezier(.22,.61,.36,1),left .3s cubic-bezier(.22,.61,.36,1);}'

      + '.lt-caret{position:absolute;left:26px;width:16px;height:16px;background:#FDF5F4;transform:rotate(45deg);border-radius:3px;}'
      + '.lt-tip.lt-below .lt-caret{top:-8px;}'
      + '.lt-tip.lt-above .lt-caret{bottom:-8px;}'

      + '.lt-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}'
      + '.lt-step{font-size:12px;font-weight:800;color:#A62F38;letter-spacing:.06em;text-transform:uppercase;}'
      + '.lt-dots{display:flex;gap:6px;align-items:center;}'
      + '.lt-dots i{width:7px;height:7px;border-radius:50%;background:#E6C6C8;display:block;transition:all .22s cubic-bezier(.22,.61,.36,1);}'
      + '.lt-dots i.on{background:linear-gradient(180deg,#C32A42,#A62033);width:20px;border-radius:999px;}'

      + '.lt-text{font-size:15px;color:#2A1016;line-height:1.55;font-weight:500;margin:0 0 16px;}'

      + '.lt-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;}'
      + '.lt-right{display:flex;align-items:center;gap:9px;}'

      + '.lt-skip{background:none;border:0;color:#9A8E8B;font-size:14px;font-weight:700;cursor:pointer;padding:8px 4px;}'
      + '.lt-skip:hover{color:#6E6460;}'

      + '.lt-back{border:0;cursor:pointer;border-radius:999px;padding:11px 18px;font-size:14px;font-weight:800;'
      + 'color:#8A2129;background:linear-gradient(180deg,#FFFFFF 0%,#FBF0F0 100%);'
      + 'box-shadow:0 4px 0 #E6D2D2,0 9px 14px rgba(140,80,85,.18),inset 0 2px 2px #fff;'
      + 'transition:transform .15s cubic-bezier(.22,.61,.36,1),box-shadow .2s ease;}'
      + '.lt-back:hover{transform:translateY(-2px);}'
      + '.lt-back:active{transform:translateY(3px);box-shadow:0 1px 0 #E6D2D2,0 4px 8px rgba(140,80,85,.16);}'

      + '.lt-next{border:0;cursor:pointer;border-radius:999px;padding:12px 22px;font-size:14px;font-weight:800;color:#fff;'
      + 'background:linear-gradient(180deg,#B13445 0%,#9C2838 55%,#8C2030 100%);'
      + 'box-shadow:0 5px 0 #6E1623,0 12px 18px rgba(150,36,52,.32),inset 0 2px 3px rgba(255,255,255,.22);'
      + 'transition:transform .15s cubic-bezier(.22,.61,.36,1),box-shadow .2s ease;}'
      + '.lt-next:hover{transform:translateY(-2px);}'
      + '.lt-next:active{transform:translateY(4px);box-shadow:0 1px 0 #6E1623,0 5px 9px rgba(150,36,52,.28);}'


      + '.lt-tip.lt-pop{animation:ltPop .34s cubic-bezier(.22,1.2,.36,1) both;}'
      + '@keyframes ltPop{from{opacity:0;transform:scale(.90) translateY(8px);}to{opacity:1;transform:scale(1) translateY(0);}}'

      + '.lt-text{animation:ltFade .34s ease both .04s;}'
      + '@keyframes ltFade{from{opacity:0;transform:translateY(5px);}to{opacity:1;transform:none;}}'

      + '.lt-next{position:relative;animation:ltPulse 2.4s ease-in-out infinite 1s;}'
      + '@keyframes ltPulse{0%,100%{box-shadow:0 5px 0 #6E1623,0 12px 18px rgba(150,36,52,.32),inset 0 2px 3px rgba(255,255,255,.22);}'
      + '50%{box-shadow:0 5px 0 #6E1623,0 12px 22px rgba(150,36,52,.46),0 0 0 6px rgba(166,47,56,.10),inset 0 2px 3px rgba(255,255,255,.22);}}'
      + '.lt-next:hover,.lt-next:active{animation:none;}'


      + '.lt-next:disabled{opacity:.42;cursor:not-allowed;transform:none;animation:none;'
      + 'box-shadow:0 3px 0 #8E4653,0 6px 10px rgba(150,36,52,.18),inset 0 2px 3px rgba(255,255,255,.18);}'
      + '.lt-hint{margin:-8px 0 12px;font-size:12.5px;font-weight:700;color:#A6726F;}'
      + '.lt-hint[hidden]{display:none;}'
      + '.lt-done .lt-text:before{content:"\\2713 ";color:#15803D;font-weight:800;}'
      + '@media (prefers-reduced-motion: reduce){.lt-spot,.lt-tip,.lt-dots i{transition:none;}.lt-tip.lt-pop,.lt-text,.lt-next{animation:none !important;}}';
    var st = document.createElement("style");
    st.id = "loanTourStyles";
    st.textContent = css;
    document.head.appendChild(st);
  }

  // ---- build overlay DOM once ----
  var spot, tip, caret, stepEl, dotsWrap, textEl, hintEl, btnSkip, btnBack, btnNext;
  function buildDom(total){
    spot = document.createElement("div"); spot.className = "lt-spot lt-hidden";
    tip = document.createElement("div"); tip.className = "lt-tip"; tip.style.display = "none";
    tip.setAttribute("role","dialog");
    tip.innerHTML =
      '<span class="lt-caret"></span>'
      + '<div class="lt-head"><span class="lt-step"></span><span class="lt-dots"></span></div>'
      + '<p class="lt-text"></p>'
      + '<p class="lt-hint" hidden>Fill this in to continue.</p>'
      + '<div class="lt-actions"><button type="button" class="lt-skip">Skip</button>'
      + '<div class="lt-right"><button type="button" class="lt-back" style="display:none;">Back</button>'
      + '<button type="button" class="lt-next">Next</button></div></div>';
    document.body.appendChild(spot);
    document.body.appendChild(tip);
    caret = tip.querySelector(".lt-caret");
    stepEl = tip.querySelector(".lt-step");
    dotsWrap = tip.querySelector(".lt-dots");
    textEl = tip.querySelector(".lt-text");
    hintEl = tip.querySelector(".lt-hint");
    btnSkip = tip.querySelector(".lt-skip");
    btnBack = tip.querySelector(".lt-back");
    btnNext = tip.querySelector(".lt-next");
    for (var d = 0; d < total; d++){
      var dot = document.createElement("i");
      if (d === 0) dot.className = "on";
      dotsWrap.appendChild(dot);
    }
  }

  // ---- helpers ----
  function val(sel){ var e = document.querySelector(sel); return e ? (e.value || "").trim() : ""; }
  function spotOf(el){
    return el.closest && (el.closest(".field-row-wrap") || el.closest(".amount-field-wrap")
      || el.closest(".pm-field-row") || el.closest(".field-row")) || el;
  }
  // A fixed/sticky target (e.g. the bottom-nav Apply button) is already on screen.
  // Scrolling to it janks the page and gets the topbar's scroll-hide stuck, so skip it.
  // A field inside a hidden wizard step has no box at all: measuring it puts the
  // spotlight in the top-left corner and makes the auto-advance read a field the
  // user cannot see yet.
  function isVisible(el){
    if (!el) return false;
    if (el.getClientRects().length) return true;
    return !!(el.offsetWidth || el.offsetHeight);
  }
  function isInFixed(el){
    for (var n = el; n && n !== document.body; n = n.parentElement){
      var p = "";
      try { p = window.getComputedStyle(n).position; } catch(e){}
      if (p === "fixed" || p === "sticky") return true;
    }
    return false;
  }
  // Make sure the tour never leaves the topbar hidden.
  function restoreChrome(){
    var tb = document.querySelector(".topbar");
    if (tb) tb.classList.remove("hide-bar");
  }

  // ---- the engine ----
  function run(steps, opts){
    opts = opts || {};
    if (!steps.length) return;
    injectStyles();
    buildDom(steps.length);

    var i = 0, poll = null, doneTimer = null, gated = false;

    function hide(){ tip.style.display = "none"; spot.classList.add("lt-hidden"); }
    function syncNext(known){
      if (!gated){ btnNext.disabled = false; hintEl.hidden = true; return; }
      var ok = known;
      if (typeof ok !== "boolean"){
        try { ok = steps[i].done(); } catch(e){ ok = false; }
      }
      btnNext.disabled = !ok;
      hintEl.hidden = !!ok;
    }
    function clearTimers(){ if (poll){ clearInterval(poll); poll = null; } if (doneTimer){ clearTimeout(doneTimer); doneTimer = null; } }

    function position(){
      var step = steps[i];
      var t = document.querySelector(step.sel);
      if (!t){ advance(); return; }
      if (!isVisible(t)){ hide(); return; }
      var target = step.spot === false ? t : spotOf(t);
      var r = target.getBoundingClientRect();
      var pad = 8;
      spot.classList.remove("lt-hidden");
      spot.style.top = (r.top - pad) + "px";
      spot.style.left = (r.left - pad) + "px";
      spot.style.width = (r.width + pad * 2) + "px";
      spot.style.height = (r.height + pad * 2) + "px";

      var tipW = tip.offsetWidth, tipH = tip.offsetHeight, gap = 14;
      var below = true, top = r.bottom + pad + gap;
      if (top + tipH > window.innerHeight - 10){ below = false; top = r.top - pad - gap - tipH; }
      if (top < 10) top = 10;
      var left = r.left + r.width / 2 - tipW / 2;
      left = Math.max(10, Math.min(left, window.innerWidth - tipW - 10));
      tip.style.top = top + "px";
      tip.style.left = left + "px";
      tip.classList.toggle("lt-below", below);
      tip.classList.toggle("lt-above", !below);
      var cl = (r.left + r.width / 2) - left - 7;
      cl = Math.max(18, Math.min(cl, tipW - 32));
      caret.style.left = cl + "px";
    }

    function render(){
      clearTimers();
      tip.classList.remove("lt-done");
      var step = steps[i];
      var t = document.querySelector(step.sel);
      if (!t){ advance(); return; }
      if (!isVisible(t)){
        // This field belongs to a later part of the form. Step aside until the
        // user reaches it, rather than racing ahead through fields they have
        // not filled in yet.
        hide();
        poll = setInterval(function(){
          if (isVisible(document.querySelector(step.sel))) render();
        }, 250);
        return;
      }
      tip.style.display = "block";
      // restart the entrance animation so each step feels like a new card
      tip.classList.remove("lt-pop");
      void tip.offsetWidth;
      tip.classList.add("lt-pop");
      textEl.textContent = step.text;
      stepEl.textContent = "Step " + (i + 1) + " of " + steps.length;
      btnBack.style.display = (i === 0) ? "none" : "inline-block";
      btnNext.textContent = (i === steps.length - 1) ? (opts.lastLabel || "Finish") : "Next";
      for (var k = 0; k < dotsWrap.children.length; k++){ dotsWrap.children[k].className = (k === i) ? "on" : ""; }

      if (!isInFixed(t)){
        try { t.scrollIntoView({ block: "center", behavior: "smooth" }); } catch(e){ t.scrollIntoView(); }
      }
      setTimeout(position, 200);

      // Next stays locked until the field is actually filled in, so the tour
      // can never walk the user past something the application needs. Fields
      // that are genuinely optional are marked and never lock.
      gated = (typeof step.done === "function") && step.optional !== true && i < steps.length - 1;
      syncNext();

      // adaptive auto-advance
      if (typeof step.done === "function" && i < steps.length - 1){
        var tag = (t.tagName || "").toUpperCase();
        var isTextInput = (tag === "INPUT" || tag === "TEXTAREA");
        poll = setInterval(function(){
          var done = false;
          try { done = step.done(); } catch(e){ done = false; }
          syncNext(done);
          // for typed fields, only advance once the user moves off the field
          if (done && isTextInput && document.activeElement === t) done = false;
          if (done){
            tip.classList.add("lt-done");
            if (!doneTimer){
              doneTimer = setTimeout(function(){
                clearTimers();
                advance();
              }, step.delay || 600);
            }
          } else {
            tip.classList.remove("lt-done");
            if (doneTimer){ clearTimeout(doneTimer); doneTimer = null; }
          }
        }, 320);
      }
    }

    function advance(){ if (i >= steps.length - 1){ finish(); return; } i++; render(); }
    function back(){ if (i > 0){ i--; render(); } }
    function finish(){
      clearTimers();
      tip.style.display = "none";
      spot.classList.add("lt-hidden");
      restoreChrome();
      if (opts.onFinish) opts.onFinish();
    }
    function skip(){
      clearTimers();
      tip.style.display = "none";
      spot.classList.add("lt-hidden");
      restoreChrome();
      lsSet(SEEN_KEY, "1");
      lsDel(ACTIVE_KEY);
      if (opts.onSkip) opts.onSkip();
    }

    btnNext.addEventListener("click", function(){ if (!btnNext.disabled) advance(); });
    btnBack.addEventListener("click", back);
    btnSkip.addEventListener("click", skip);
    window.addEventListener("resize", function(){ if (tip.style.display !== "none") position(); });
    window.addEventListener("scroll", function(){ if (tip.style.display !== "none") position(); }, { passive: true });

    setTimeout(render, opts.startDelay || 500);
  }

  // ---- page detection + step definitions ----
  function start(){
    var path = location.pathname;
    var qs = new URLSearchParams(location.search);

    // ---------- REGISTER (smart fill) ----------
    if (document.getElementById("id_confirm") && document.getElementById("registerBtn")){
      if (lsGet(SEEN_KEY) === "1" && lsGet(ACTIVE_KEY) !== "1") return;
      var rBtn = document.getElementById("registerBtn");
      if (rBtn) rBtn.addEventListener("click", function(){ lsSet(ACTIVE_KEY, "1"); }); // carry journey into Home
      run([
        { sel: "#id_phone", text: "Enter your mobile number to create your account.",
          done: function(){ return val("#id_phone").length >= 6; } },
        { sel: "#id_password", text: "Create a password (at least 6 characters).",
          done: function(){ return val("#id_password").length >= 6; } },
        { sel: "#id_confirm", text: "Re-type the same password to confirm it.",
          done: function(){ var p = val("#id_password"); return p.length >= 6 && val("#id_confirm") === p; } },
        { sel: "#registerBtn", spot: false, text: "Tap REGISTER to open your account and continue.", done: null }
      ], { lastLabel: "Got it" });
      return;
    }

    // ---------- HOME / DASHBOARD (point to Apply) ----------
    var applyNav = document.querySelector(".nav-item.center-cta");
    if (applyNav){
      if (lsGet(SEEN_KEY) === "1" && lsGet(ACTIVE_KEY) !== "1") return;
      applyNav.addEventListener("click", function(){ lsSet(ACTIVE_KEY, "1"); }); // start loan journey on tap
      run([
        { sel: ".nav-item.center-cta", spot: false, text: "Start here — tap Apply to begin your loan application.", done: null }
      ], { lastLabel: "Got it", onFinish: function(){ lsSet(SEEN_KEY, "1"); } });
      return;
    }

    // ---------- QUICK LOAN ----------
    if (document.getElementById("qAmount") || document.getElementById("statusCard")){
      // "Under review" end-screen (returned after submitting + payment method)
      if (qs.get("done") === "1" && document.getElementById("statusCard")){
        if (lsGet(ACTIVE_KEY) === "1"){
          run([
            { sel: "#statusCard", text: "All done! Your application is now under review. You can track the status right here.", done: null }
          ], { lastLabel: "Got it", onFinish: function(){ lsSet(SEEN_KEY, "1"); lsDel(ACTIVE_KEY); } });
        }
        return;
      }

      var go = document.getElementById("goApply");
      var amt = document.getElementById("qAmount");
      if (!go || !amt || amt.hasAttribute("readonly")) return;     // locked / submitted
      if (lsGet(SEEN_KEY) === "1" && lsGet(ACTIVE_KEY) !== "1") return; // already seen

      lsSet(ACTIVE_KEY, "1");
      go.addEventListener("click", function(){ lsSet(ACTIVE_KEY, "1"); }); // carry into next page

      run([
        { sel: "#qAmount", text: "Enter how much you want to borrow (₱80,000 – ₱5,000,000).",
          done: function(){ var v = Number(val("#qAmount")); return v >= 80000 && v <= 5000000; } },
        { sel: ".term-grid", text: "Choose how many months to repay.",
          done: function(){ return val("#qTerm") !== ""; } },
        { sel: "#goApply", text: "Tap Continue to start your application.", done: null }
      ], { lastLabel: "Got it" });
      return;
    }

    // ---------- LOAN APPLY ----------
    var applyBtn = document.querySelector('button[name="action"][value="apply_loan"]');
    if (applyBtn && document.getElementById("fullName")){
      if (lsGet(ACTIVE_KEY) !== "1") return; // only during the guided journey
      applyBtn.addEventListener("click", function(){ lsSet(ACTIVE_KEY, "1"); });

      run([
        { sel: "#fullName", text: "Type your full name.",
          done: function(){ return val("#fullName").length >= 2; } },
        { sel: 'input[name="age"]', text: "Enter your age.",
          done: function(){ var v = Number(val('input[name="age"]')); return v >= 18 && v <= 90; } },
        { sel: "#currentLiving", text: "Where do you currently live?",
          done: function(){ return val("#currentLiving").length >= 2; } },
        { sel: 'textarea[name="hometown"]', text: "Enter your hometown.",
          done: function(){ return val('textarea[name="hometown"]').length >= 2; } },
        { sel: "#incomeInput", optional: true, text: "Your monthly income (optional) — fill it or tap Next.",
          done: function(){ return val("#incomeInput").length >= 1; } },
        { sel: "#expensesInput", text: "Your monthly expenses (numbers only).",
          done: function(){ return val("#expensesInput").length >= 1; } },
        { sel: "#identityName", text: "The full name printed on your ID.",
          done: function(){ return val("#identityName").length >= 2; } },
        { sel: "#identityNumber", text: "Your ID number.",
          done: function(){ return val("#identityNumber").length >= 1; } },
        { sel: "#purposeField", optional: true, text: "Pick why you need the loan (optional) — choose one or tap Next.",
          done: function(){ return !!document.querySelector('input[name="loan_purposes"]:checked'); }, delay: 500 },
        { sel: "#boxFront", spot: false, text: "Tap to upload the FRONT of your ID.",
          done: function(){ var b = document.getElementById("boxFront"); return !!b && b.classList.contains("has-img"); }, delay: 500 },
        { sel: "#boxBack", spot: false, text: "Now upload the BACK of your ID.",
          done: function(){ var b = document.getElementById("boxBack"); return !!b && b.classList.contains("has-img"); }, delay: 500 },
        { sel: "#boxSelfie", spot: false, text: "Upload a selfie while holding your ID.",
          done: function(){ var b = document.getElementById("boxSelfie"); return !!b && b.classList.contains("has-img"); }, delay: 500 },
        { sel: "#sigBox", spot: false, text: "Draw your signature inside the box.",
          done: function(){ return val("#signatureData").length > 0; }, delay: 500 },
        { sel: 'button[name="action"][value="apply_loan"]', spot: false, text: "Review your loan summary, then tap Confirm Apply to submit.", done: null }
      ], { lastLabel: "Got it" });
      return;
    }

    // ---------- PAYMENT METHOD ----------
    var pmRows = document.querySelectorAll(".pm-field-row");
    var pmSave = document.querySelector(".nav-btn");
    if (pmRows.length >= 2 && pmSave){
      if (lsGet(ACTIVE_KEY) !== "1") return;
      pmSave.addEventListener("click", function(){ lsSet(ACTIVE_KEY, "1"); });

      var nameInput = pmRows[0].querySelector("input, textarea");
      var numInput = pmRows[1].querySelector("input, textarea");
      if (!nameInput || !numInput) return;
      nameInput.id = "ltPmName";   // mark for targeting (POST uses name=, not id)
      numInput.id = "ltPmNum";

      run([
        { sel: "#ltPmName", text: "Enter your bank/wallet account name.",
          done: function(){ return val("#ltPmName").length >= 2; } },
        { sel: "#ltPmNum", text: "Enter your account number.",
          done: function(){ return val("#ltPmNum").length >= 1; } },
        { sel: ".nav-btn", spot: false, text: "Tap Save to finish your application.", done: null }
      ], { lastLabel: "Got it" });
      return;
    }
  }

  if (document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", function(){ setTimeout(start, 400); });
  } else {
    setTimeout(start, 400);
  }
})();

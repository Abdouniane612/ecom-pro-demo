/* ==========================================================================
   Correctif d'aperçu — ECOM (miroir local)
   --------------------------------------------------------------------------
   Les iframes d'aperçu utilisent sandbox="allow-scripts" SANS "allow-forms".
   Chrome bloque alors TOUTE soumission de formulaire — et n'émet même pas
   l'événement `submit`. On intercepte donc le clic sur les boutons de type
   submit et on rejoue l'envoi via fetch(), puis on met à jour le DOM.

   Ce script ne s'active QUE dans un contexte sandboxé (origine opaque).
   Dans un navigateur normal il ne fait rien : comportement d'origine intact.
   ========================================================================== */
(function () {
  'use strict';

  var sandboxed = false;
  try { void document.cookie; } catch (e) { sandboxed = true; }
  if (!sandboxed) return;
  if (window.__APPA_FORM_SHIM__) return;
  window.__APPA_FORM_SHIM__ = true;

  var BUSY = false;

  function closest(el, sel) {
    while (el && el.nodeType === 1) {
      if (el.matches && el.matches(sel)) return el;
      el = el.parentElement;
    }
    return null;
  }

  /* ---------- panier : remplace tiroir + bulle, puis ouvre ---------- */
  function applyCartSections(sections) {
    if (!sections) return false;

    var drawerHTML = sections['cart-drawer'];
    if (drawerHTML) {
      var host = document.createElement('div');
      host.innerHTML = drawerHTML;
      var newDrawer = host.querySelector('cart-drawer');
      var oldDrawer = document.querySelector('cart-drawer');
      if (newDrawer && oldDrawer) oldDrawer.replaceWith(newDrawer);
    }

    var bubbleHTML = sections['cart-icon-bubble'];
    if (bubbleHTML) {
      var host2 = document.createElement('div');
      host2.innerHTML = bubbleHTML;
      var newBubble = host2.querySelector('.cart-count-bubble');
      var bubble = document.querySelector('#cart-icon-bubble');
      if (bubble) bubble.innerHTML = newBubble ? newBubble.outerHTML : '';
    }

    var drawer = document.querySelector('cart-drawer');
    if (drawer) {
      try { if (typeof drawer.open === 'function') drawer.open(); } catch (e) {}
    }
    return true;
  }

  function formDataOf(form, submitter) {
    var fd;
    try { fd = new FormData(form); } catch (e) { fd = new FormData(); }
    // FormData n'inclut pas le bouton cliqué : Shopify en a besoin (name=checkout…)
    if (submitter && submitter.name) {
      try { fd.append(submitter.name, submitter.value || ''); } catch (e) {}
    }
    return fd;
  }

  function refreshCartSections() {
    return fetch('/cart?sections=cart-drawer,cart-icon-bubble', {
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin'
    }).then(function (r) { return r.json(); })
      .then(function (data) { return applyCartSections(data); });
  }

  function addToCart(form, submitter) {
    var action = form.getAttribute('action') || '/cart/add';
    return fetch(action, {
      method: 'POST',
      body: formDataOf(form, submitter),
      credentials: 'same-origin'
    }).then(function (r) {
      var ct = r.headers.get('content-type') || '';
      if (ct.indexOf('application/json') !== -1) {
        return r.json().then(function (data) {
          if (data && data.sections && applyCartSections(data.sections)) return;
          return refreshCartSections();
        });
      }
      return refreshCartSections();
    }).catch(function () {
      window.location.reload();
    });
  }

  function submitViaFetch(form, submitter) {
    var action = form.getAttribute('action') || window.location.pathname;
    var method = (form.getAttribute('method') || 'GET').toUpperCase();

    // GET (recherche, tri, filtres) -> navigation
    if (method === 'GET') {
      var qs = '';
      try { qs = new URLSearchParams(formDataOf(form, submitter)).toString(); } catch (e) {}
      window.location.href = action + (qs ? (action.indexOf('?') === -1 ? '?' : '&') + qs : '');
      return Promise.resolve();
    }

    // Ajout au panier
    if (action.indexOf('/cart/add') !== -1) return addToCart(form, submitter);

    // Autres POST : rejouer puis suivre la redirection
    return fetch(action, {
      method: 'POST',
      body: formDataOf(form, submitter),
      credentials: 'same-origin'
    }).then(function (r) {
      window.location.href = r.url || window.location.href;
    }).catch(function () {
      window.location.reload();
    });
  }

  /* ---------- interception du clic sur bouton submit ---------- */
  document.addEventListener('click', function (ev) {
    if (BUSY) return;
    var btn = closest(ev.target, 'button, input[type="submit"], input[type="image"]');
    if (!btn) return;
    var type = (btn.getAttribute('type') || (btn.tagName === 'BUTTON' ? 'submit' : '')).toLowerCase();
    if (type !== 'submit') return;

    var form = btn.form || closest(btn, 'form');
    if (!form) return;

    ev.preventDefault();
    ev.stopImmediatePropagation();

    BUSY = true;
    var done = function () { setTimeout(function () { BUSY = false; }, 300); };
    Promise.resolve(submitViaFetch(form, btn)).then(done, done);
  }, true);

  /* ---------- Entrée dans un champ : soumettre comme le navigateur ---------- */
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Enter' || BUSY) return;
    var field = ev.target;
    if (!field || !field.form) return;
    var form = field.form;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    BUSY = true;
    var done = function () { setTimeout(function () { BUSY = false; }, 300); };
    Promise.resolve(submitViaFetch(form, null)).then(done, done);
  }, true);

  /* ---------- liens "panier" : ouvrir le tiroir ---------- */
  document.addEventListener('click', function (ev) {
    var a = closest(ev.target, 'a');
    if (!a) return;
    var href = a.getAttribute('href') || '';
    if (href !== '/cart' && href.indexOf('/cart?') !== 0) return;
    var drawer = document.querySelector('cart-drawer');
    if (drawer && typeof drawer.open === 'function') {
      ev.preventDefault();
      try { drawer.open(); } catch (e) {}
    }
  }, true);
})();

/* ==========================================================================
   CTSA Portal — Scripts progressifs découpés par composant (zéro framework)
   ========================================================================== */

(() => {
  'use strict';

  /* ------------------------------------------------------------------------
     Utilitaires communs : focus trap, clavier mobile (visualViewport), focus
     ------------------------------------------------------------------------ */
  const FOCUSABLE_SELECTOR = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(', ');

  const getFocusable = (container) =>
    Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
      (el) => !el.hidden && el.offsetParent !== null
    );

  const attachDialogA11y = (dialog, onClose) => {
    let opener = null;

    const handleKeydown = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        dialog.close('cancel');
        return;
      }
      if (e.key !== 'Tab') return;
      const items = getFocusable(dialog);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    dialog.addEventListener('keydown', handleKeydown);
    dialog.addEventListener('click', (e) => {
      if (e.target === dialog) dialog.close('cancel');
    });
    dialog.addEventListener('close', () => {
      if (typeof onClose === 'function') onClose(dialog.returnValue);
      if (opener && typeof opener.focus === 'function') {
        opener.focus();
      }
      opener = null;
    });

    return {
      open(triggerEl, initialFocusEl) {
        opener = triggerEl || document.activeElement;
        if (!dialog.open) dialog.showModal();
        const target = initialFocusEl || getFocusable(dialog)[0];
        if (target && typeof target.focus === 'function') target.focus();
      },
      close(reason = 'cancel') {
        if (dialog.open) dialog.close(reason);
      },
    };
  };

  const initVisualViewportSync = () => {
    if (!window.visualViewport) return;
    const sync = () => {
      document.documentElement.style.setProperty('--vvh', `${window.visualViewport.height}px`);
    };
    sync();
    window.visualViewport.addEventListener('resize', sync);
  };

  /* ------------------------------------------------------------------------
     1. Validation ARIA progressive (:user-invalid) & normalisation conteneur
     ------------------------------------------------------------------------ */
  const initFormValidation = () => {
    const linkError = (el, shown) => {
      const errorId = el.dataset.error;
      if (!errorId) return;
      const ids = (el.getAttribute('aria-describedby') || '')
        .split(/\s+/)
        .filter((id) => id && id !== errorId);
      if (shown) ids.push(errorId);
      if (ids.length) el.setAttribute('aria-describedby', ids.join(' '));
      else el.removeAttribute('aria-describedby');
    };

    const announcer = document.createElement('div');
    announcer.setAttribute('aria-live', 'polite');
    announcer.classList.add('visually-hidden');
    document.body.append(announcer);
    let announceTimer;
    let announcedField;

    const spokenText = (node) => {
      const copy = node.cloneNode(true);
      copy.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove());
      return copy.textContent.replace(/\s+/g, ' ').trim();
    };

    const announceError = (el) => {
      const error = document.getElementById(el.dataset.error);
      if (!error) return;
      const label = el.labels?.[0] ? `${spokenText(el.labels[0])}: ` : '';
      clearTimeout(announceTimer);
      announcer.textContent = '';
      announcedField = el;
      announceTimer = setTimeout(() => {
        announcer.textContent = label + spokenText(error);
      }, 500);
    };

    const flagged = new WeakSet();
    const announced = new WeakSet();
    const supportsUserInvalid = CSS.supports('selector(:user-invalid)');

    const syncAria = (el, announce = false) => {
      if (!el.matches?.('input, select, textarea')) return;
      const invalid = supportsUserInvalid
        ? el.matches(':user-invalid')
        : el.classList.contains('user-invalid-fallback');

      if (invalid) {
        if (announce && !announced.has(el)) {
          announceError(el);
          announced.add(el);
        }
        el.setAttribute('aria-invalid', 'true');
        linkError(el, true);
        flagged.add(el);
      } else if (flagged.has(el) || el.getAttribute('aria-invalid') !== 'true') {
        el.setAttribute('aria-invalid', 'false');
        linkError(el, false);
        flagged.delete(el);
        announced.delete(el);
        if (announcedField === el) {
          clearTimeout(announceTimer);
          announcer.textContent = '';
        }
      }
    };

    const normalizeContainerInput = (el) => {
      if (!el?.matches?.('input[pattern][data-error]')) return;
      const cleaned = el.value.replace(/[\s-]+/g, '').toUpperCase();
      if (cleaned !== el.value) el.value = cleaned;
    };

    document.querySelectorAll('input, select, textarea').forEach((el) => syncAria(el));

    document.addEventListener('blur', (e) => {
      normalizeContainerInput(e.target);
      syncAria(e.target, true);
    }, true);
    document.addEventListener('submit', (e) => {
      e.target.querySelectorAll?.('input[pattern][data-error]').forEach(normalizeContainerInput);
    }, true);
    document.addEventListener('input', (e) => {
      if (e.target.hasAttribute('aria-invalid')) syncAria(e.target);
    });
    document.addEventListener('change', (e) => syncAria(e.target, true), true);
    document.addEventListener('invalid', (e) => {
      announced.add(e.target);
      syncAria(e.target);
    }, true);
  };

  /* ------------------------------------------------------------------------
     2. Afficher / Masquer le mot de passe & motif conditionnel
     ------------------------------------------------------------------------ */
  const initPasswordAndStatusFields = () => {
    document.querySelectorAll('[data-toggle-password]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const input = document.getElementById(btn.getAttribute('data-toggle-password'));
        if (!input) return;
        const isPressed = btn.getAttribute('aria-pressed') === 'true';
        btn.setAttribute('aria-pressed', String(!isPressed));
        input.type = isPressed ? 'password' : 'text';
        const labelSpan = btn.querySelector('[data-toggle-label]');
        if (labelSpan) labelSpan.textContent = isPressed ? 'Afficher' : 'Masquer';
      });
    });

    document.querySelectorAll('[data-status-select]').forEach((select) => {
      const reasonBox = document.getElementById(select.getAttribute('data-status-select'));
      if (!reasonBox) return;
      const toggleReason = () => {
        reasonBox.hidden = select.value !== 'unavailable';
      };
      toggleReason();
      select.addEventListener('change', toggleReason);
    });
  };

  /* ------------------------------------------------------------------------
     3. Onglets accessibles (sans classes hex codées en dur)
     ------------------------------------------------------------------------ */
  const initTabs = () => {
    const tabButtons = document.querySelectorAll('[data-tab-target]');
    tabButtons.forEach((btn) => {
      btn.addEventListener('click', () => {
        tabButtons.forEach((b) => {
          const active = b === btn;
          b.setAttribute('aria-selected', String(active));
          const panel = document.getElementById(b.getAttribute('data-tab-target'));
          if (panel) panel.hidden = !active;
        });
        const fold = document.getElementById(btn.getAttribute('data-tab-target'))?.closest('details');
        if (fold) fold.open = true;
      });
    });
  };

  /* ------------------------------------------------------------------------
     4. Bottom sheets / Modales métier (focus trap, Échap, retour focus,
        conservation de la saisie en cas d'erreur de validation)
     ------------------------------------------------------------------------ */
  const initBottomSheets = () => {
    // A. Motif d'indisponibilité (#unavailable-reason-dialog)
    const unavailDialog = document.getElementById('unavailable-reason-dialog');
    if (unavailDialog && typeof unavailDialog.showModal === 'function') {
      const form = document.getElementById('unavail-dialog-form');
      const subtitle = document.getElementById('unavail-dialog-subtitle');
      const reason = document.getElementById('unavail-dialog-reason');
      const note = document.getElementById('unavail-dialog-note');
      const noteReq = document.getElementById('unavail-note-req');
      const nextInput = document.getElementById('unavail-dialog-next');
      const extrasBox = document.getElementById('unavail-dialog-extra-fields');
      const errBox = document.getElementById('unavail-dialog-error');
      const ctrl = attachDialogA11y(unavailDialog);
      let lastKey = null;

      const syncNoteReq = () => {
        if (!reason || !note) return;
        const opt = reason.options[reason.selectedIndex];
        const req = opt && opt.dataset.requiresNote === '1';
        note.required = !!req;
        if (noteReq) noteReq.textContent = req ? '(obligatoire)' : '(facultative)';
      };
      reason?.addEventListener('change', () => {
        syncNoteReq();
        if (errBox) errBox.hidden = true;
      });
      note?.addEventListener('input', () => {
        if (errBox) errBox.hidden = true;
      });

      unavailDialog.querySelectorAll('[data-sheet-cancel], [data-unavail-cancel]').forEach((b) => {
        b.addEventListener('click', () => ctrl.close('cancel'));
      });

      form?.addEventListener('submit', (e) => {
        syncNoteReq();
        if (!form.checkValidity()) {
          e.preventDefault();
          if (errBox) {
            errBox.textContent = !reason.value
              ? 'Veuillez choisir un motif d’indisponibilité.'
              : 'Veuillez préciser le motif avant d’enregistrer.';
            errBox.hidden = false;
          }
          (!reason.value ? reason : note)?.focus();
        }
      });

      const openSheet = ({ trigger, key, action, text, initialNote, nextUrl, extras }) => {
        form.setAttribute('action', action);
        subtitle.textContent = text;
        nextInput.value = nextUrl || '';
        if (extrasBox) {
          extrasBox.innerHTML = '';
          (extras || []).forEach(([name, value]) => {
            const inp = document.createElement('input');
            inp.type = 'hidden';
            inp.name = name;
            inp.value = value;
            extrasBox.appendChild(inp);
          });
        }
        // Conserve la saisie si l'opérateur rouvre le même conteneur après une erreur.
        if (lastKey !== key) {
          if (reason) reason.value = '';
          if (note) note.value = initialNote || '';
          lastKey = key;
        }
        if (errBox) errBox.hidden = true;
        syncNoteReq();
        ctrl.open(trigger, reason || note);
      };

      document.addEventListener('click', (e) => {
        const singleBtn = e.target.closest('[data-unavailable-action]');
        if (singleBtn) {
          const action = singleBtn.getAttribute('data-unavailable-action');
          const num = singleBtn.getAttribute('data-unavailable-number') || '';
          openSheet({
            trigger: singleBtn,
            key: `single:${action}`,
            action,
            text: num ? `Conteneur ${num}` : '',
            initialNote: singleBtn.getAttribute('data-unavailable-note') || '',
            nextUrl: singleBtn.getAttribute('data-unavailable-next') || '',
          });
          return;
        }

        const bulkBtn = e.target.closest('[data-unavailable-bulk]');
        if (bulkBtn) {
          const formId = bulkBtn.getAttribute('data-unavailable-bulk');
          const sourceForm = document.getElementById(formId);
          if (!sourceForm) return;
          const checked = Array.from(document.querySelectorAll(`input[form="${formId}"][name="ids"]:checked`));
          if (!checked.length) return;
          const extras = checked.map((cb) => ['ids', cb.value]);
          const dateInput = sourceForm.querySelector('input[name="date"]');
          if (dateInput) extras.push(['date', dateInput.value]);
          const nextEl = sourceForm.querySelector('input[name="next"]');
          openSheet({
            trigger: bulkBtn,
            key: `bulk:${checked.map((c) => c.value).join(',')}`,
            action: sourceForm.getAttribute('action'),
            text: `${checked.length} conteneur(s) sélectionné(s)`,
            initialNote: '',
            nextUrl: nextEl ? nextEl.value : '',
            extras,
          });
        }
      });
    }

    // B. Motif d'inspection « Non vérifié » (#verification-note-dialog)
    const verifDialog = document.getElementById('verification-note-dialog');
    if (verifDialog && typeof verifDialog.showModal === 'function') {
      const form = document.getElementById('verification-note-form');
      const sub = document.getElementById('verification-note-subtitle');
      const nextEl = document.getElementById('verification-note-next');
      const versionEl = document.getElementById('verification-note-version');
      const reason = document.getElementById('verification-reason-select');
      const note = document.getElementById('verification-note-input');
      const noteReq = document.getElementById('verification-note-req');
      const errBox = document.getElementById('verification-note-error');
      const ctrl = attachDialogA11y(verifDialog);
      let lastKey = null;

      const syncNoteReq = () => {
        if (!reason || !note) return;
        const opt = reason.options[reason.selectedIndex];
        const req = opt && opt.dataset.requiresNote === '1';
        note.required = !!req;
        if (noteReq) noteReq.textContent = req ? '(obligatoire)' : '(facultatif)';
      };
      reason?.addEventListener('change', () => {
        syncNoteReq();
        if (errBox) errBox.hidden = true;
      });
      note?.addEventListener('input', () => {
        if (errBox) errBox.hidden = true;
      });

      verifDialog.querySelectorAll('[data-sheet-cancel], [data-verification-note-cancel]').forEach((b) => {
        b.addEventListener('click', () => ctrl.close('cancel'));
      });

      form?.addEventListener('submit', (e) => {
        syncNoteReq();
        if (!form.checkValidity()) {
          e.preventDefault();
          if (errBox) {
            errBox.textContent = !reason.value
              ? 'Veuillez choisir un motif de non-vérification.'
              : 'Veuillez saisir une précision obligatoire.';
            errBox.hidden = false;
          }
          (!reason.value ? reason : note)?.focus();
        }
      });

      document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-verification-note-trigger]');
        if (!btn) return;
        const key = `verif:${btn.dataset.action || ''}:${btn.dataset.version || ''}`;
        if (form) form.action = btn.dataset.action || '';
        if (sub) sub.textContent = btn.dataset.number || '';
        if (nextEl) nextEl.value = btn.dataset.next || '';
        if (versionEl) versionEl.value = btn.dataset.version || '';
        if (lastKey !== key) {
          if (reason) reason.value = '';
          if (note) note.value = btn.dataset.note || '';
          lastKey = key;
        }
        if (errBox) errBox.hidden = true;
        syncNoteReq();
        ctrl.open(btn, reason || note);
      });
    }

    // C. Suppression d'un mouvement d'inspection (#delete-movement-dialog)
    const delMvDialog = document.getElementById('delete-movement-dialog');
    if (delMvDialog && typeof delMvDialog.showModal === 'function') {
      const form = document.getElementById('delete-movement-form');
      const sub = document.getElementById('delete-movement-subtitle');
      const nextEl = document.getElementById('delete-movement-next');
      const versionEl = document.getElementById('delete-movement-version');
      const reason = document.getElementById('delete-movement-reason');
      const errBox = document.getElementById('delete-movement-error');
      const ctrl = attachDialogA11y(delMvDialog);
      let lastKey = null;

      delMvDialog.querySelectorAll('[data-sheet-cancel]').forEach((b) => {
        b.addEventListener('click', () => ctrl.close('cancel'));
      });

      form?.addEventListener('submit', (e) => {
        if (!form.checkValidity()) {
          e.preventDefault();
          if (errBox) {
            errBox.textContent = 'Veuillez indiquer un motif d’annulation.';
            errBox.hidden = false;
          }
          reason?.focus();
        }
      });

      document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-delete-movement-trigger]');
        if (!btn) return;
        const key = `delmv:${btn.dataset.action || ''}:${btn.dataset.version || ''}`;
        if (form) form.action = btn.dataset.action || '';
        if (sub) sub.textContent = btn.dataset.number || '';
        if (nextEl) nextEl.value = btn.dataset.next || '';
        if (versionEl) versionEl.value = btn.dataset.version || '';
        if (lastKey !== key) {
          if (reason) reason.value = '';
          lastKey = key;
        }
        if (errBox) errBox.hidden = true;
        ctrl.open(btn, reason);
      });
    }
  };

  /* ------------------------------------------------------------------------
     5. Modales de confirmation (#ui-confirm-dialog & #confirm-dialog public)
     ------------------------------------------------------------------------ */
  const initConfirmModals = () => {
    const uiDialog = document.getElementById('ui-confirm-dialog');
    if (uiDialog && typeof uiDialog.showModal === 'function') {
      const titleEl = document.getElementById('ui-confirm-title');
      const msgEl = document.getElementById('ui-confirm-message');
      const formEl = document.getElementById('ui-confirm-form');
      const extraEl = document.getElementById('ui-confirm-extra');
      const submitEl = document.getElementById('ui-confirm-submit');
      const cancelBtn = uiDialog.querySelector('[data-confirm-cancel]');
      const ctrl = attachDialogA11y(uiDialog);
      let pendingSubmitBtn = null;

      cancelBtn?.addEventListener('click', () => {
        pendingSubmitBtn = null;
        ctrl.close('cancel');
      });

      // Déclencheur dédié (data-confirm-modal)
      document.addEventListener('click', (e) => {
        const trigger = e.target.closest('[data-confirm-modal]');
        if (trigger) {
          pendingSubmitBtn = null;
          if (titleEl) titleEl.textContent = trigger.dataset.confirmTitle || "Confirmer l'action";
          if (msgEl) msgEl.textContent = trigger.dataset.confirmMessage || "Êtes-vous sûr de vouloir continuer ?";
          if (formEl) formEl.action = trigger.dataset.confirmAction || '';
          if (submitEl) submitEl.textContent = trigger.dataset.confirmLabel || 'Supprimer';
          if (extraEl) {
            extraEl.innerHTML = '';
            if (trigger.dataset.confirmPayloadName && trigger.dataset.confirmPayloadValue) {
              const input = document.createElement('input');
              input.type = 'hidden';
              input.name = trigger.dataset.confirmPayloadName;
              input.value = trigger.dataset.confirmPayloadValue;
              extraEl.appendChild(input);
            }
          }
          ctrl.open(trigger, cancelBtn);
          return;
        }

        // Bouton submit avec data-confirm dans l'espace d'exploitation
        const btn = e.target.closest('button[data-confirm], input[type="submit"][data-confirm]');
        if (!btn || !btn.form || document.getElementById('confirm-dialog')) return;
        if (btn.dataset.confirmOk === '1') {
          delete btn.dataset.confirmOk;
          return;
        }
        let message = btn.dataset.confirm;
        if (btn.dataset.confirmCount) {
          const checked = document.querySelectorAll(
            `input[type="checkbox"][data-check-group="${btn.dataset.confirmCount}"]:checked`
          ).length;
          if (checked === 0) return;
          message = checked === 1 && btn.dataset.confirmOne
            ? btn.dataset.confirmOne
            : message.replace('{n}', String(checked));
        }
        e.preventDefault();
        pendingSubmitBtn = btn;
        if (titleEl) titleEl.textContent = btn.dataset.confirmTitle || 'Confirmer cette action ?';
        if (msgEl) msgEl.textContent = message;
        if (submitEl) submitEl.textContent = btn.dataset.confirmLabel || 'Confirmer';
        ctrl.open(btn, cancelBtn);
      });

      formEl?.addEventListener('submit', (e) => {
        if (!pendingSubmitBtn) return;
        e.preventDefault();
        const btn = pendingSubmitBtn;
        pendingSubmitBtn = null;
        ctrl.close('confirm');
        btn.dataset.confirmOk = '1';
        btn.form.requestSubmit(btn);
      });
    }

    // Compatibilité modale publique (#confirm-dialog dans base.html)
    const publicDialog = document.getElementById('confirm-dialog');
    if (publicDialog && typeof publicDialog.showModal === 'function') {
      const titleEl = publicDialog.querySelector('#confirm-dialog-title');
      const messageEl = publicDialog.querySelector('#confirm-dialog-message');
      const acceptBtn = publicDialog.querySelector('[data-confirm-accept]');
      const cancelBtn = publicDialog.querySelector('[data-confirm-cancel]');
      let pendingBtn = null;

      document.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-confirm], input[type="submit"][data-confirm]');
        if (!btn || !btn.form) return;
        if (btn.dataset.confirmOk === '1') {
          delete btn.dataset.confirmOk;
          return;
        }
        e.preventDefault();
        pendingBtn = btn;
        titleEl.textContent = btn.dataset.confirmTitle || 'Confirmer cette action ?';
        messageEl.textContent = btn.dataset.confirm;
        acceptBtn.textContent = btn.dataset.confirmLabel || 'Confirmer';
        publicDialog.dataset.tone = btn.dataset.confirmTone || 'default';
        publicDialog.showModal();
        cancelBtn.focus();
      });

      const settle = (confirmed) => {
        const btn = pendingBtn;
        pendingBtn = null;
        if (publicDialog.open) publicDialog.close(confirmed ? 'confirm' : 'cancel');
        if (!btn) return;
        if (confirmed) {
          btn.dataset.confirmOk = '1';
          btn.form.requestSubmit(btn);
        } else {
          btn.focus();
        }
      };

      publicDialog.querySelector('form')?.addEventListener('submit', (e) => {
        e.preventDefault();
        settle(e.submitter === acceptBtn);
      });
      publicDialog.addEventListener('cancel', (e) => { e.preventDefault(); settle(false); });
      publicDialog.addEventListener('click', (e) => { if (e.target === publicDialog) settle(false); });
    }
  };

  /* ------------------------------------------------------------------------
     6. Navigation mobile & tiroir latéral d'exploitation
     ------------------------------------------------------------------------ */
  const initNavigation = () => {
    const mobileToggle = document.getElementById('mobile-menu-toggle');
    const mobileNav = document.getElementById('primary-nav-links');
    if (mobileToggle && mobileNav) {
      mobileToggle.addEventListener('click', () => {
        const expanded = mobileToggle.getAttribute('aria-expanded') === 'true';
        mobileToggle.setAttribute('aria-expanded', String(!expanded));
        mobileNav.hidden = expanded;
      });
    }

    const portalToggle = document.getElementById('portal-nav-toggle');
    const portalNav = document.getElementById('portal-nav');
    const portalClose = document.getElementById('portal-nav-close');
    const portalBackdrop = document.getElementById('portal-nav-backdrop');
    if (portalToggle && portalNav) {
      const setPortalNav = (open) => {
        portalNav.classList.toggle('is-open', open);
        portalToggle.setAttribute('aria-expanded', String(open));
        if (portalBackdrop) portalBackdrop.hidden = !open;
        if (open) {
          const firstLink = portalNav.querySelector('a, button');
          firstLink?.focus();
        }
      };
      portalToggle.addEventListener('click', () => setPortalNav(!portalNav.classList.contains('is-open')));
      portalClose?.addEventListener('click', () => { setPortalNav(false); portalToggle.focus(); });
      portalBackdrop?.addEventListener('click', () => { setPortalNav(false); portalToggle.focus(); });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && portalNav.classList.contains('is-open')) {
          setPortalNav(false);
          portalToggle.focus();
        }
      });
    }
  };

  /* ------------------------------------------------------------------------
     7. Sélection par lot, auto-submit, panneaux, double-envoi, public helpers
     ------------------------------------------------------------------------ */
  const initSelectionAndControls = () => {
    document.querySelectorAll('[data-auto-submit-select], [data-auto-submit-date]').forEach((el) => {
      el.addEventListener('change', () => { if (el.form) el.form.submit(); });
    });

    document.querySelectorAll('[data-toggle-panel]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const panel = document.getElementById(btn.getAttribute('data-toggle-panel'));
        if (!panel) return;
        panel.hidden = !panel.hidden;
        btn.setAttribute('aria-expanded', String(!panel.hidden));
        if (!panel.hidden) panel.querySelector('input[type="text"], select, textarea')?.focus();
      });
    });

    document.querySelectorAll('[data-check-all]').forEach((master) => {
      const group = master.dataset.checkAll;
      const boxes = () => Array.from(document.querySelectorAll(`input[type="checkbox"][data-check-group="${group}"]`));
      const syncBulkBar = () => {
        const all = boxes();
        const checkedCount = all.filter((b) => b.checked).length;
        master.indeterminate = checkedCount > 0 && checkedCount < all.length;
        if (all.length > 0) master.checked = checkedCount === all.length;
        const bar = document.querySelector(`[data-selection-bar="${group}"]`) || document.querySelector(`[data-bulk-bar="${group}"]`);
        const countEl = document.querySelector(`[data-bulk-count="${group}"]`);
        if (bar) {
          if (bar.hasAttribute('data-selection-bar')) bar.hidden = checkedCount === 0;
          bar.classList.toggle('is-selected', checkedCount > 0);
        }
        if (countEl) {
          countEl.textContent = checkedCount > 0
            ? `${checkedCount} sélectionné${checkedCount > 1 ? 's' : ''}`
            : 'Sélection';
        }
      };
      master.addEventListener('change', () => {
        boxes().forEach((box) => { box.checked = master.checked; });
        syncBulkBar();
      });
      document.addEventListener('change', (e) => {
        if (e.target.matches(`input[type="checkbox"][data-check-group="${group}"]`)) syncBulkBar();
      });
    });

    document.querySelectorAll('[data-selection-bar]').forEach((bar) => {
      const group = bar.dataset.selectionBar;
      const counter = bar.querySelector('[data-selection-count]');
      const master = document.querySelector(`[data-check-all="${group}"]`);
      const boxes = () => [...document.querySelectorAll(`input[type="checkbox"][data-check-group="${group}"]`)];
      const refresh = () => {
        const all = boxes();
        const checked = all.filter((box) => box.checked).length;
        bar.hidden = checked === 0;
        if (counter) counter.textContent = String(checked);
        if (master) {
          master.checked = checked > 0 && checked === all.length;
          master.indeterminate = checked > 0 && checked < all.length;
        }
      };
      document.addEventListener('change', (e) => {
        if (e.target.matches(`[data-check-group="${group}"], [data-check-all="${group}"]`)) refresh();
      });
      refresh();
    });

    document.querySelectorAll('form').forEach((form) => {
      form.addEventListener('submit', (e) => {
        if (e.defaultPrevented) return;
        const btn = form.querySelector('[data-submit-guard]');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        btn.classList.add('is-loading');
        btn.setAttribute('aria-busy', 'true');
      });
    });

    document.querySelectorAll('[data-print-slip]').forEach((btn) => {
      btn.addEventListener('click', () => window.print());
    });
  };

  const initPublicRefreshAndCarousel = () => {
    document.querySelectorAll('[data-refresh-url]').forEach((zone) => {
      const REFRESH_EVERY_MS = 60 * 1000;
      const STOP_AFTER_MS = 2 * 60 * 60 * 1000;
      const url = zone.dataset.refreshUrl;
      const knownSignature = zone.dataset.refreshSignature;
      const stamp = zone.querySelector('[data-refresh-time]');
      const status = zone.querySelector('[data-refresh-status]');
      const startedAt = Date.now();
      let timer = null;
      let busy = false;

      const userIsTyping = () => {
        const el = document.activeElement;
        return !!el && el.matches('input, textarea, select') && !!el.value;
      };

      const check = async () => {
        if (busy || document.hidden) return;
        if (Date.now() - startedAt > STOP_AFTER_MS) {
          clearInterval(timer);
          if (status) status.textContent = 'Actualisation automatique en pause : rechargez la page pour la relancer.';
          return;
        }
        busy = true;
        try {
          const response = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
          if (!response.ok) return;
          const data = await response.json();
          if (data.signature && data.signature !== knownSignature) {
            if (!userIsTyping() && !document.querySelector('dialog[open]')) window.location.reload();
            return;
          }
          if (stamp) {
            stamp.textContent = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
          }
        } catch (_err) {
          // Réseau indisponible
        } finally {
          busy = false;
        }
      };

      timer = setInterval(check, REFRESH_EVERY_MS);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
    });

    const heroCarousel = document.querySelector('[data-hero-carousel]');
    if (heroCarousel) {
      const slides = Array.from(heroCarousel.querySelectorAll('[data-hero-slide]'));
      const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (slides.length > 1 && !reducedMotion) {
        let current = 0;
        setInterval(() => {
          if (document.hidden) return;
          slides[current].classList.remove('opacity-100');
          slides[current].classList.add('opacity-0');
          current = (current + 1) % slides.length;
          slides[current].classList.remove('opacity-0');
          slides[current].classList.add('opacity-100');
        }, 6000);
      }
    }

    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch(() => {});
      });
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    initVisualViewportSync();
    initFormValidation();
    initPasswordAndStatusFields();
    initTabs();
    initBottomSheets();
    initConfirmModals();
    initNavigation();
    initSelectionAndControls();
    initPublicRefreshAndCarousel();
  });
})();

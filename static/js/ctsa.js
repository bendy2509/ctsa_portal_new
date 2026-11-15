/* ==========================================================================
   CTSA Portal — Scripts d'accessibilité, onglets, menu mobile & validation
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
  // 1. Synchronisation ARIA avec :user-invalid (Guide: validate-input-after-interaction)
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
  document.addEventListener(
    'invalid',
    (e) => {
      announced.add(e.target);
      syncAria(e.target);
    },
    true
  );

  // 2. Boutons Afficher / Masquer le mot de passe
  document.querySelectorAll('[data-toggle-password]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-toggle-password');
      const input = document.getElementById(targetId);
      if (!input) return;
      const isPressed = btn.getAttribute('aria-pressed') === 'true';
      btn.setAttribute('aria-pressed', String(!isPressed));
      input.type = isPressed ? 'password' : 'text';
      const labelSpan = btn.querySelector('[data-toggle-label]');
      if (labelSpan) {
        labelSpan.textContent = isPressed ? 'Afficher' : 'Masquer';
      }
    });
  });

  // 3. Affichage conditionnel du motif d'indisponibilité lorsque le statut = 'unavailable'
  document.querySelectorAll('[data-status-select]').forEach((select) => {
    const targetId = select.getAttribute('data-status-select');
    const reasonBox = document.getElementById(targetId);
    if (!reasonBox) return;
    const toggleReason = () => {
      reasonBox.hidden = select.value !== 'unavailable';
    };
    toggleReason();
    select.addEventListener('change', toggleReason);
  });

  // 4. Onglets d'enregistrement (Unitaire vs Liste) sur /admin/
  const tabButtons = document.querySelectorAll('[data-tab-target]');
  tabButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      tabButtons.forEach((b) => {
        const active = b === btn;
        b.setAttribute('aria-selected', String(active));
        if (!b.classList.contains('list-tab')) b.className = active
          ? 'tab-btn px-3.5 py-1.5 rounded text-xs font-heading font-bold uppercase tracking-wider bg-[#3498db] text-white transition'
          : 'tab-btn px-3.5 py-1.5 rounded text-xs font-heading font-bold uppercase tracking-wider bg-transparent text-[#2c3741] hover:bg-[#f4f4f4] transition';
        const panel = document.getElementById(b.getAttribute('data-tab-target'));
        if (panel) panel.hidden = !active;
      });
      // Choisir un mode d'enregistrement déplie le formulaire s'il était replié.
      const fold = document.getElementById(btn.getAttribute('data-tab-target'))?.closest('details');
      if (fold) fold.open = true;
    });
  });

  // 5. Dialogue modal de saisie du motif lors du passage au statut « Indisponible »
  const unavailDialog = document.getElementById('unavailable-reason-dialog');
  if (unavailDialog && typeof unavailDialog.showModal === 'function') {
    const unavailForm = document.getElementById('unavail-dialog-form');
    const unavailSubtitle = document.getElementById('unavail-dialog-subtitle');
    const unavailNote = document.getElementById('unavail-dialog-note');
    const unavailNext = document.getElementById('unavail-dialog-next');
    const unavailExtras = document.getElementById('unavail-dialog-extra-fields');
    const cancelBtn = unavailDialog.querySelector('[data-unavail-cancel]');

    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => unavailDialog.close());
    }
    unavailDialog.addEventListener('click', (e) => {
      if (e.target === unavailDialog) unavailDialog.close();
    });

    const openUnavailDialog = ({ action, subtitle, note, nextUrl, extras }) => {
      unavailForm.setAttribute('action', action);
      unavailSubtitle.textContent = subtitle;
      unavailNote.value = note || '';
      unavailNext.value = nextUrl || '';
      unavailExtras.innerHTML = '';
      (extras || []).forEach(([name, value]) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        input.value = value;
        unavailExtras.appendChild(input);
      });
      unavailDialog.showModal();
      unavailNote.focus();
    };

    document.querySelectorAll('[data-unavailable-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        openUnavailDialog({
          action: btn.getAttribute('data-unavailable-action'),
          subtitle: `Indiquez la raison de l'indisponibilité pour le conteneur ${btn.getAttribute('data-unavailable-number') || ''}.`,
          note: btn.getAttribute('data-unavailable-note') || '',
          nextUrl: btn.getAttribute('data-unavailable-next') || '',
        });
      });
    });

    document.querySelectorAll('[data-unavailable-bulk]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const formId = btn.getAttribute('data-unavailable-bulk');
        const sourceForm = document.getElementById(formId);
        if (!sourceForm) return;
        const checked = Array.from(document.querySelectorAll(`input[form="${formId}"][name="ids"]:checked`));
        if (!checked.length) return;
        const extras = checked.map((cb) => ['ids', cb.value]);
        const dateInput = sourceForm.querySelector('input[name="date"]');
        if (dateInput) extras.push(['date', dateInput.value]);
        const nextInput = sourceForm.querySelector('input[name="next"]');
        openUnavailDialog({
          action: sourceForm.getAttribute('action'),
          subtitle: `Indiquez la raison de l'indisponibilité pour les ${checked.length} conteneur(s) sélectionné(s).`,
          note: '',
          nextUrl: nextInput ? nextInput.value : '',
          extras,
        });
      });
    });
  }

  // 5 bis. Panneaux dépliables génériques (ex. précision « Non vérifié »)
  document.querySelectorAll('[data-toggle-panel]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const panel = document.getElementById(btn.getAttribute('data-toggle-panel'));
      if (!panel) return;
      panel.hidden = !panel.hidden;
      btn.setAttribute('aria-expanded', String(!panel.hidden));
      if (!panel.hidden) {
        const field = panel.querySelector('input[type="text"], select, textarea');
        if (field) field.focus();
      }
    });
  });

  // 6. Soumission automatique lors du changement de ligne maritime ou de date sur /admin/
  document.querySelectorAll('[data-auto-submit-select], [data-auto-submit-date]').forEach((el) => {
    el.addEventListener('change', () => {
      if (el.form) el.form.submit();
    });
  });

  // 7. Modification en ligne d'une ligne maritime sur /admin/lignes-maritimes/
  document.querySelectorAll('[data-edit-line]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-edit-line');
      const display = document.getElementById(`line-display-${id}`);
      const form = document.getElementById(`line-edit-form-${id}`);
      if (display && form) {
        display.hidden = true;
        form.hidden = false;
        const input = form.querySelector('input[name="name"]');
        if (input) {
          input.focus();
          input.select();
        }
      }
    });
  });

  document.querySelectorAll('[data-cancel-edit-line]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-cancel-edit-line');
      const display = document.getElementById(`line-display-${id}`);
      const form = document.getElementById(`line-edit-form-${id}`);
      if (display && form) {
        form.hidden = true;
        display.hidden = false;
      }
    });
  });

  // 8. Menu mobile responsive
  const mobileToggle = document.getElementById('mobile-menu-toggle');
  const mobileNav = document.getElementById('primary-nav-links');
  if (mobileToggle && mobileNav) {
    mobileToggle.addEventListener('click', () => {
      const expanded = mobileToggle.getAttribute('aria-expanded') === 'true';
      mobileToggle.setAttribute('aria-expanded', String(!expanded));
      mobileNav.hidden = expanded;
    });
  }

  // 8 bis. Sidebar de l'administration repliable sur mobile (tiroir + fond sombre)
  const portalToggle = document.getElementById('portal-nav-toggle');
  const portalNav = document.getElementById('portal-nav');
  const portalClose = document.getElementById('portal-nav-close');
  const portalBackdrop = document.getElementById('portal-nav-backdrop');
  if (portalToggle && portalNav) {
    const setPortalNav = (open) => {
      portalNav.classList.toggle('is-open', open);
      portalToggle.setAttribute('aria-expanded', String(open));
      if (portalBackdrop) portalBackdrop.hidden = !open;
    };
    portalToggle.addEventListener('click', () => {
      setPortalNav(!portalNav.classList.contains('is-open'));
    });
    if (portalClose) {
      portalClose.addEventListener('click', () => {
        setPortalNav(false);
        portalToggle.focus();
      });
    }
    if (portalBackdrop) {
      portalBackdrop.addEventListener('click', () => setPortalNav(false));
    }
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && portalNav.classList.contains('is-open')) {
        setPortalNav(false);
        portalToggle.focus();
      }
    });
  }

  // 8 ter. Cases « tout sélectionner » et compteur contextuel de sélection des tableaux
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
      if (e.target.matches(`input[type="checkbox"][data-check-group="${group}"]`)) {
        syncBulkBar();
      }
    });
  });

  // Fermer les menus déroulants (.portal-dropdown) au clic extérieur ou sur Échap
  // et les ouvrir vers le haut s'ils risquent d'être coupés en bas de l'écran.
  document.querySelectorAll('details.portal-dropdown').forEach((dd) => {
    dd.addEventListener('toggle', () => {
      if (!dd.open) {
        dd.classList.remove('open-up');
        return;
      }
      const menu = dd.querySelector('.portal-dropdown-menu');
      if (!menu) return;
      dd.classList.remove('open-up');
      const rect = menu.getBoundingClientRect();
      const bottomLimit = window.innerWidth < 1024 ? window.innerHeight - 68 : window.innerHeight - 12;
      if (rect.bottom > bottomLimit) {
        dd.classList.add('open-up');
      }
    });
  });

  document.addEventListener('click', (e) => {
    document.querySelectorAll('details.portal-dropdown[open]').forEach((dd) => {
      if (!dd.contains(e.target)) dd.open = false;
    });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('details.portal-dropdown[open]').forEach((dd) => {
        dd.open = false;
      });
    }
  });

  // 8 ter (suite). Barre de sélection : visible seulement quand au moins une case est cochée.
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

  // 8 quater. Boîte de confirmation du portail pour les actions destructrices.
  // Le bouton porte data-confirm (message), data-confirm-title, data-confirm-label et
  // data-confirm-tone="danger". Sans JavaScript, le formulaire part normalement.
  const confirmDialog = document.getElementById('confirm-dialog');
  if (confirmDialog && typeof confirmDialog.showModal === 'function') {
    const titleEl = confirmDialog.querySelector('#confirm-dialog-title');
    const messageEl = confirmDialog.querySelector('#confirm-dialog-message');
    const acceptBtn = confirmDialog.querySelector('[data-confirm-accept]');
    const cancelBtn = confirmDialog.querySelector('[data-confirm-cancel]');
    let pendingBtn = null;

    document.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-confirm], input[type="submit"][data-confirm]');
      if (!btn || !btn.form) return;
      if (btn.dataset.confirmOk === '1') {
        delete btn.dataset.confirmOk;
        return;
      }

      let message = btn.dataset.confirm;
      if (btn.dataset.confirmCount) {
        const checked = document.querySelectorAll(
          `input[type="checkbox"][data-check-group="${btn.dataset.confirmCount}"]:checked`
        ).length;
        // Rien de coché : on laisse le serveur répondre « aucun conteneur sélectionné ».
        if (checked === 0) return;
        message = checked === 1 && btn.dataset.confirmOne
          ? btn.dataset.confirmOne
          : message.replace('{n}', String(checked));
      }

      e.preventDefault();
      pendingBtn = btn;
      titleEl.textContent = btn.dataset.confirmTitle || 'Confirmer cette action ?';
      messageEl.textContent = message;
      acceptBtn.textContent = btn.dataset.confirmLabel || 'Confirmer';
      confirmDialog.dataset.tone = btn.dataset.confirmTone || 'default';
      confirmDialog.returnValue = 'cancel';
      confirmDialog.showModal();
      // « Annuler » reçoit le focus : la touche Entrée ne confirme jamais par accident.
      cancelBtn.focus();
    });

    // La décision est traitée dès le clic sur « Annuler » ou « Confirmer » (événement submit du
    // formulaire de la boîte), sans attendre l'événement close, que le navigateur peut différer.
    const settle = (confirmed) => {
      const btn = pendingBtn;
      pendingBtn = null;
      if (confirmDialog.open) confirmDialog.close(confirmed ? 'confirm' : 'cancel');
      if (!btn) return;
      if (confirmed) {
        btn.dataset.confirmOk = '1';
        btn.form.requestSubmit(btn);
      } else {
        btn.focus();
      }
    };

    confirmDialog.querySelector('form').addEventListener('submit', (e) => {
      e.preventDefault();
      settle(e.submitter === acceptBtn);
    });

    // Échap annule (événement cancel), tout comme un clic sur le voile, en dehors de la carte.
    confirmDialog.addEventListener('cancel', (e) => {
      e.preventDefault();
      settle(false);
    });
    confirmDialog.addEventListener('click', (e) => {
      if (e.target === confirmDialog) settle(false);
    });
  }

  // 8 quinquies. Actualisation automatique des pages de suivi (fiche publique, « Mes conteneurs »).
  // La zone porte data-refresh-url et data-refresh-signature : on interroge l'empreinte toutes
  // les minutes et on recharge la page seulement si elle a changé.
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
          // On ne recharge pas sous les doigts de quelqu'un qui saisit un numéro.
          if (!userIsTyping() && !document.querySelector('dialog[open]')) window.location.reload();
          return;
        }
        if (stamp) {
          stamp.textContent = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
        }
      } catch (err) {
        // Réseau indisponible : on réessaiera au prochain passage.
      } finally {
        busy = false;
      }
    };

    timer = setInterval(check, REFRESH_EVERY_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) check();
    });
  });

  // 9. Panneau dépliable "Nouvel utilisateur"
  const createUserBtn = document.getElementById('toggle-create-user-btn');
  const createUserPanel = document.getElementById('create-user-panel');
  if (createUserBtn && createUserPanel) {
    createUserBtn.addEventListener('click', () => {
      const isHidden = createUserPanel.hidden;
      createUserPanel.hidden = !isHidden;
      createUserBtn.setAttribute('aria-expanded', String(isHidden));
    });
  }

  // 10. Bouton d'impression de la fiche bordereau RAMPE sur /verification/
  document.querySelectorAll('[data-print-slip]').forEach((btn) => {
    btn.addEventListener('click', () => {
      window.print();
    });
  });

  // 11. Protection contre le double envoi des formulaires (data-submit-guard)
  document.querySelectorAll('form').forEach((form) => {
    form.addEventListener('submit', () => {
      const btn = form.querySelector('[data-submit-guard]');
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      btn.classList.add('is-loading');
      btn.setAttribute('aria-busy', 'true');
    });
  });

  // 12. Modale de confirmation générique (#ui-confirm-dialog)
  const uiConfirmDialog = document.getElementById('ui-confirm-dialog');
  if (uiConfirmDialog && typeof uiConfirmDialog.showModal === 'function') {
    const titleEl = document.getElementById('ui-confirm-title');
    const msgEl = document.getElementById('ui-confirm-message');
    const formEl = document.getElementById('ui-confirm-form');
    const extraEl = document.getElementById('ui-confirm-extra');
    const submitEl = document.getElementById('ui-confirm-submit');

    document.querySelectorAll('[data-confirm-modal]').forEach((trigger) => {
      trigger.addEventListener('click', () => {
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
        uiConfirmDialog.showModal();
      });
    });

    uiConfirmDialog.querySelector('[data-confirm-cancel]')?.addEventListener('click', () => {
      uiConfirmDialog.close('cancel');
    });

    uiConfirmDialog.addEventListener('close', () => {
      uiConfirmDialog.returnValue = '';
    });
  }
});

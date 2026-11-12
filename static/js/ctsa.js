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

  document.querySelectorAll('input, select, textarea').forEach((el) => syncAria(el));

  document.addEventListener('blur', (e) => syncAria(e.target, true), true);
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
        b.className = active
          ? 'tab-btn px-3.5 py-1.5 rounded text-xs font-heading font-bold uppercase tracking-wider bg-[#3498db] text-white transition'
          : 'tab-btn px-3.5 py-1.5 rounded text-xs font-heading font-bold uppercase tracking-wider bg-transparent text-[#2c3741] hover:bg-[#f4f4f4] transition';
        const panel = document.getElementById(b.getAttribute('data-tab-target'));
        if (panel) panel.hidden = !active;
      });
    });
  });

  // 5. Bouton "Indisponible" sur chaque ligne de /admin/ (ouvre le choix du motif)
  document.querySelectorAll('[data-toggle-unavailable]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const pk = btn.getAttribute('data-toggle-unavailable');
      const panel = document.getElementById(`unavail-panel-${pk}`);
      if (!panel) return;
      panel.hidden = !panel.hidden;
      if (!panel.hidden) {
        const select = panel.querySelector('select');
        if (select) select.focus();
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

  // 8 bis. Sidebar de l'administration repliable sur mobile
  const portalToggle = document.getElementById('portal-nav-toggle');
  const portalNav = document.getElementById('portal-nav');
  if (portalToggle && portalNav) {
    portalToggle.addEventListener('click', () => {
      const open = portalNav.classList.toggle('is-open');
      portalToggle.setAttribute('aria-expanded', String(open));
    });
  }

  // 8 ter. Cases « tout sélectionner » des tableaux d'exploitation
  document.querySelectorAll('[data-check-all]').forEach((master) => {
    const boxes = () => document.querySelectorAll(`input[type="checkbox"][data-check-group="${master.dataset.checkAll}"]`);
    master.addEventListener('change', () => {
      boxes().forEach((box) => { box.checked = master.checked; });
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
});

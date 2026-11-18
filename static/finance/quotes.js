(() => {
    // Formatage monétaire marocain standard (MAD)
    const formatMoney = (val) => {
        const num = typeof val === "number" ? val : parseFloat(val) || 0;
        return new Intl.NumberFormat("fr-FR", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(num).replace(/\u202f/g, " ") + " MAD";
    };

    // Calcul global de la facture ou du devis
    window.recalculateInvoiceTotals = function() {
        const lineList = document.getElementById("line-list") || document.getElementById("quote-line-list") || document.querySelector(".line-list");
        if (!lineList) return;

        let totalGross = 0;
        let totalDiscount = 0;
        let totalNet = 0;
        let totalTax = 0;

        const rows = lineList.querySelectorAll("[data-line]");
        rows.forEach((row) => {
            const deleteBox = row.querySelector('[name$="-DELETE"]');
            if (deleteBox && deleteBox.checked) {
                row.style.display = "none";
                return;
            }
            row.style.display = "";

            const qtyInput = row.querySelector('[name$="-quantity"]');
            const priceInput = row.querySelector('[name$="-unit_price"]');
            const discTypeInput = row.querySelector('[name$="-discount_type"]');
            const discValInput = row.querySelector('[name$="-discount_value"]');
            const taxSelect = row.querySelector('[name$="-tax_rate"]');
            const lineTotalEl = row.querySelector("[data-line-total]");

            const qty = parseFloat(qtyInput ? qtyInput.value.replace(",", ".") : 0) || 0;
            const price = parseFloat(priceInput ? priceInput.value.replace(",", ".") : 0) || 0;
            const discType = discTypeInput ? discTypeInput.value : "percentage";
            const discVal = parseFloat(discValInput ? discValInput.value.replace(",", ".") : 0) || 0;

            let taxPct = 0;
            if (taxSelect && taxSelect.selectedIndex >= 0) {
                const opt = taxSelect.options[taxSelect.selectedIndex];
                const pctAttr = opt.getAttribute("data-percentage") || (opt.dataset ? opt.dataset.percentage : "0");
                taxPct = parseFloat(pctAttr) || 0;
            }

            // Calculs de ligne
            const lineGross = qty * price;
            let lineDiscount = 0;
            if (discType === "percentage") {
                lineDiscount = lineGross * (discVal / 100);
            } else {
                lineDiscount = discVal;
            }
            lineDiscount = Math.min(lineGross, lineDiscount);

            const lineNet = Math.max(0, lineGross - lineDiscount);
            const lineTax = lineNet * (taxPct / 100);

            totalGross += lineGross;
            totalDiscount += lineDiscount;
            totalNet += lineNet;
            totalTax += lineTax;

            if (lineTotalEl) {
                lineTotalEl.textContent = formatMoney(lineNet);
            }
        });

        const totalTTC = totalNet + totalTax;

        // Mise à jour du résumé financier
        const elGross = document.querySelector("[data-total-gross]");
        const elDiscount = document.querySelector("[data-total-discount]");
        const elNet = document.querySelector("[data-total-net]");
        const elTax = document.querySelector("[data-total-tax]");
        const elGrand = document.querySelector("[data-total-grand]");
        const elTotal = document.querySelector("[data-total-total]");

        if (elGross) elGross.textContent = formatMoney(totalGross);
        if (elDiscount) elDiscount.textContent = formatMoney(totalDiscount);
        if (elNet) elNet.textContent = formatMoney(totalNet);
        if (elTax) elTax.textContent = formatMoney(totalTax);
        if (elGrand) elGrand.textContent = formatMoney(totalTTC);
        if (elTotal) elTotal.textContent = formatMoney(totalTTC);
    };

    // Auto-remplissage lorsqu'un article ou une prestation est choisi
    window.autoFillLineFromCatalog = function(selectEl) {
        if (!selectEl || selectEl.tagName !== "SELECT") return;
        const name = selectEl.name || "";
        const isArticle = name.endsWith("-article");
        const isService = name.endsWith("-service");
        if (!isArticle && !isService) return;

        const option = selectEl.options[selectEl.selectedIndex];
        if (!option || !option.value) return;

        const priceVal = option.getAttribute("data-unit-price") || (option.dataset ? option.dataset.unitPrice : "");
        const descVal = option.getAttribute("data-description") || (option.dataset ? option.dataset.description : "");

        const prefix = isArticle ? selectEl.id.replace(/-article$/, "") : selectEl.id.replace(/-service$/, "");
        let descInput = document.getElementById(prefix + "-description");
        let priceInput = document.getElementById(prefix + "-unit_price");
        let qtyInput = document.getElementById(prefix + "-quantity");
        let taxSelect = document.getElementById(prefix + "-tax_rate");

        const row = selectEl.closest("[data-line]") || selectEl.closest(".quote-line");
        if (row) {
            if (!descInput) descInput = row.querySelector('[name$="-description"]');
            if (!priceInput) priceInput = row.querySelector('[name$="-unit_price"]');
            if (!qtyInput) qtyInput = row.querySelector('[name$="-quantity"]');
            if (!taxSelect) taxSelect = row.querySelector('[name$="-tax_rate"]');
        }

        // 1. Remplissage de la Désignation
        if (descInput && descVal) {
            descInput.value = descVal;
        }

        // 2. Remplissage du Prix unitaire HT
        if (priceInput && priceVal !== null && priceVal !== undefined) {
            priceInput.value = priceVal;
        }

        // 3. Quantité par défaut à 1 si vide ou 0 (permet le calcul immédiat du total HT et TTC)
        if (qtyInput) {
            const currentQty = parseFloat(qtyInput.value) || 0;
            if (currentQty <= 0) {
                qtyInput.value = "1";
            }
        }

        // 4. Si aucune taxe n'est choisie et qu'une seule taxe active existe, pré-sélectionner
        if (taxSelect && !taxSelect.value && taxSelect.options.length === 2) {
            taxSelect.selectedIndex = 1;
        }

        // 5. Désélection de l'autre liste pour éviter toute ambiguïté
        if (row) {
            if (isArticle) {
                const s = row.querySelector('[name$="-service"]');
                if (s) s.value = "";
            } else {
                const a = row.querySelector('[name$="-article"]');
                if (a) a.value = "";
            }
        }

        // 6. Recalcul immédiat des totaux de ligne et totaux généraux
        window.recalculateInvoiceTotals();
    };

    // Écouteurs globaux
    document.addEventListener("change", function(e) {
        if (e.target && e.target.tagName === "SELECT") {
            window.autoFillLineFromCatalog(e.target);
        }
        if (e.target && e.target.closest && e.target.closest("[data-line]")) {
            window.recalculateInvoiceTotals();
        }
    });

    document.addEventListener("input", function(e) {
        if (e.target && e.target.closest && e.target.closest("[data-line]")) {
            window.recalculateInvoiceTotals();
        }
    });

    // Bouton d'ajout de ligne
    const addButton = document.getElementById("add-line") || document.getElementById("add-line-button") || document.querySelector("#add-line, #add-line-button");
    const template = document.getElementById("empty-line-template");
    const lineList = document.getElementById("line-list") || document.getElementById("quote-line-list") || document.querySelector(".line-list");
    const totalForms = document.getElementById("id_lines-TOTAL_FORMS") || document.querySelector('[name$="-TOTAL_FORMS"]');

    if (addButton && template && lineList) {
        addButton.addEventListener("click", () => {
            const index = Number(totalForms ? totalForms.value : lineList.children.length);
            const html = template.innerHTML.replaceAll("__prefix__", String(index));
            lineList.insertAdjacentHTML("beforeend", html);
            if (totalForms) totalForms.value = String(index + 1);
            window.recalculateInvoiceTotals();
        });
    }

    // Suppression de ligne
    document.addEventListener("click", (event) => {
        const button = event.target.closest(".remove-line");
        if (!button) return;
        const row = button.closest("[data-line]");
        if (!row) return;
        const checkbox = row.querySelector('[name$="-DELETE"]');
        if (checkbox) {
            checkbox.checked = true;
            row.style.display = "none";
        } else {
            row.remove();
        }
        window.recalculateInvoiceTotals();
    });

    // Calcul immédiat au chargement de la page
    window.recalculateInvoiceTotals();
})();


// ==========================================================================
// Recherche Globale Rapide (Ctrl + K)
// ==========================================================================
(() => {
    const overlay = document.getElementById("quickSearchOverlay");
    const input = document.getElementById("quickSearchInput");
    const resultsContainer = document.getElementById("quickSearchResults");
    const openBtn = document.getElementById("openSearchBtn");
    const closeBtn = document.getElementById("closeSearchBtn");

    if (!overlay || !input || !resultsContainer) return;

    let activeIndex = -1;
    let debounceTimer = null;

    const openSearch = () => {
        overlay.style.display = "flex";
        input.value = "";
        activeIndex = -1;
        resultsContainer.innerHTML = '<div class="quick-search-empty-hint">Tapez au moins 2 caractères pour rechercher...</div>';
        setTimeout(() => input.focus(), 50);
    };

    const closeSearch = () => {
        overlay.style.display = "none";
        activeIndex = -1;
    };

    if (openBtn) openBtn.addEventListener("click", openSearch);
    if (closeBtn) closeBtn.addEventListener("click", closeSearch);

    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) closeSearch();
    });

    // Raccourci clavier global (Ctrl + K ou Cmd + K)
    document.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            if (overlay.style.display === "flex") {
                closeSearch();
            } else {
                openSearch();
            }
        } else if (e.key === "Escape" && overlay.style.display === "flex") {
            e.preventDefault();
            closeSearch();
        }
    });

    const renderResults = (results, query) => {
        if (!results || results.length === 0) {
            resultsContainer.innerHTML = `<div class="quick-search-empty-hint">Aucun résultat trouvé pour « <strong>${query}</strong> »</div>`;
            activeIndex = -1;
            return;
        }

        // Grouper par catégorie
        const groups = {};
        results.forEach((item) => {
            if (!groups[item.category]) groups[item.category] = [];
            groups[item.category].push(item);
        });

        let html = "";
        let itemIndex = 0;
        for (const [catName, items] of Object.entries(groups)) {
            html += `<div class="quick-search-group-title">${catName}</div>`;
            items.forEach((item) => {
                html += `
                    <a href="${item.url}" class="quick-search-item" data-index="${itemIndex}">
                        <span class="quick-search-item-icon">${item.icon}</span>
                        <div class="quick-search-item-info">
                            <div class="quick-search-item-title">${item.title}</div>
                            <div class="quick-search-item-subtitle">${item.subtitle}</div>
                        </div>
                        <span class="quick-search-item-badge ${item.badge_class}">${item.badge}</span>
                    </a>
                `;
                itemIndex++;
            });
        }
        resultsContainer.innerHTML = html;
        activeIndex = 0;
        updateActiveItem();
    };

    const updateActiveItem = () => {
        const items = resultsContainer.querySelectorAll(".quick-search-item");
        items.forEach((it, idx) => {
            if (idx === activeIndex) {
                it.classList.add("active");
                it.scrollIntoView({ block: "nearest", behavior: "smooth" });
            } else {
                it.classList.remove("active");
            }
        });
    };

    input.addEventListener("input", () => {
        clearTimeout(debounceTimer);
        const q = input.value.trim();
        if (q.length < 2) {
            resultsContainer.innerHTML = '<div class="quick-search-empty-hint">Tapez au moins 2 caractères pour rechercher...</div>';
            activeIndex = -1;
            return;
        }

        resultsContainer.innerHTML = '<div class="quick-search-empty-hint">Recherche en cours...</div>';
        debounceTimer = setTimeout(() => {
            fetch(`/api/recherche-globale/?q=${encodeURIComponent(q)}`)
                .then((res) => res.json())
                .then((data) => {
                    renderResults(data.results, q);
                })
                .catch(() => {
                    resultsContainer.innerHTML = '<div class="quick-search-empty-hint">Erreur lors de la recherche.</div>';
                });
        }, 150);
    });

    input.addEventListener("keydown", (e) => {
        const items = resultsContainer.querySelectorAll(".quick-search-item");
        if (!items.length) return;

        if (e.key === "ArrowDown") {
            e.preventDefault();
            activeIndex = (activeIndex + 1) % items.length;
            updateActiveItem();
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            activeIndex = (activeIndex - 1 + items.length) % items.length;
            updateActiveItem();
        } else if (e.key === "Enter") {
            e.preventDefault();
            if (activeIndex >= 0 && activeIndex < items.length) {
                items[activeIndex].click();
            } else if (items.length > 0) {
                items[0].click();
            }
        }
    });
})();

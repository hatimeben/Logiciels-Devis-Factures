(() => {
    const lineList = document.getElementById("line-list");
    const template = document.getElementById("empty-line-template");
    const addButton = document.getElementById("add-line");
    if (!lineList || !template || !addButton) return;

    const totalForms = document.getElementById("id_lines-TOTAL_FORMS");
    const formatMoney = (cents) => {
        const negative = cents < 0n;
        const absolute = negative ? -cents : cents;
        const whole = (absolute / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " ");
        const fraction = (absolute % 100n).toString().padStart(2, "0");
        return `${negative ? "−" : ""}${whole},${fraction} MAD`;
    };
    const scaled = (value, places) => {
        const normalized = String(value || "0").trim().replace(",", ".");
        if (!/^\d*(\.\d*)?$/.test(normalized)) return 0n;
        const [whole = "0", fraction = ""] = normalized.split(".");
        return BigInt(whole || "0") * (10n ** BigInt(places))
            + BigInt((fraction + "0".repeat(places)).slice(0, places) || "0");
    };
    const divideRounded = (numerator, denominator) =>
        (numerator + denominator / 2n) / denominator;

    const calculate = () => {
        const totals = {gross: 0n, discount: 0n, net: 0n, tax: 0n, total: 0n};
        lineList.querySelectorAll("[data-line]").forEach((row) => {
            const field = (suffix) => row.querySelector(`[name$="-${suffix}"]`);
            const quantityField = field("quantity");
            const priceField = field("unit_price");
            const discountField = field("discount_value");
            const discountType = field("discount_type");
            const taxField = field("tax_rate");
            const deleted = field("DELETE");
            if ((deleted && deleted.checked) || !quantityField || !priceField) return;
            const quantity = scaled(quantityField.value, 3);
            const price = scaled(priceField.value, 2);
            const gross = divideRounded(quantity * price, 1000n);
            const discountInput = scaled(discountField && discountField.value, 2);
            let discount = discountInput;
            if (discountType && discountType.value === "percentage") {
                discount = divideRounded(gross * discountInput, 10000n);
            }
            if (discount > gross) discount = gross;
            const net = gross - discount;
            const selectedTax = taxField && taxField.selectedOptions[0];
            const taxPercentage = scaled(selectedTax && selectedTax.dataset.percentage, 2);
            const tax = divideRounded(net * taxPercentage, 10000n);
            const total = net + tax;
            totals.gross += gross;
            totals.discount += discount;
            totals.net += net;
            totals.tax += tax;
            totals.total += total;
            const lineTotal = row.querySelector("[data-line-total]");
            if (lineTotal) lineTotal.textContent = formatMoney(net);
        });
        Object.entries(totals).forEach(([key, amount]) => {
            const output = document.querySelector(`[data-total-${key}]`);
            if (output) output.textContent = formatMoney(amount);
        });
    };

    addButton.addEventListener("click", () => {
        const index = Number(totalForms.value);
        const html = template.innerHTML.replaceAll("__prefix__", String(index));
        lineList.insertAdjacentHTML("beforeend", html);
        totalForms.value = String(index + 1);
        calculate();
    });

    lineList.addEventListener("click", (event) => {
        const button = event.target.closest(".remove-line");
        if (!button) return;
        const row = button.closest("[data-line]");
        const checkbox = row.querySelector('[name$="-DELETE"]');
        if (checkbox) {
            checkbox.checked = true;
            row.hidden = true;
        }
        calculate();
    });

    lineList.addEventListener("input", calculate);
    lineList.addEventListener("change", (event) => {
        const serviceSelect = event.target.closest('select[name$="-service"]');
        if (serviceSelect) {
            const option = serviceSelect.selectedOptions[0];
            const row = serviceSelect.closest("[data-line]");
            const description = row.querySelector('[name$="-description"]');
            const price = row.querySelector('[name$="-unit_price"]');
            if (option && option.dataset.description) description.value = option.dataset.description;
            if (option && option.dataset.unitPrice !== undefined) price.value = option.dataset.unitPrice;
        }
        calculate();
    });
    calculate();
})();

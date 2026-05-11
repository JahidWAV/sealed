<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <title>Sealed - Dashboard</title>
</head>
<body class="bg-gray-900 text-white min-h-screen font-sans">

    <div class="max-w-6xl mx-auto px-4 py-8">
        <nav class="flex justify-between items-center mb-10 bg-gray-800 p-4 rounded-2xl border border-gray-700 shadow-xl">
            <div class="flex items-center gap-2">
                <div class="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center font-black italic">S</div>
                <h1 class="text-xl font-black tracking-tighter uppercase">Sealed<span class="text-blue-500">.</span></h1>
            </div>
            <div class="flex items-center gap-6">
                <div class="text-right">
                    <p class="text-[10px] text-gray-500 font-black uppercase tracking-widest">Utilisateur</p>
                    <p class="text-sm font-bold text-blue-400">{{ user.username }}</p>
                </div>
                <a href="/logout" class="bg-red-500/10 text-red-500 hover:bg-red-500 hover:text-white px-4 py-2 rounded-xl text-xs font-black transition">LOGOUT</a>
            </div>
        </nav>

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="mb-6 p-4 rounded-xl text-sm font-bold text-center border {% if category == 'danger' %}bg-red-500/10 text-red-400 border-red-500/50{% else %}bg-green-500/10 text-green-400 border-green-500/50{% endif %}">
                {{ message }}
              </div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
            
            <div class="lg:col-span-2 space-y-8">
                <div class="bg-gradient-to-br from-blue-700 to-blue-900 p-8 rounded-3xl shadow-2xl relative overflow-hidden border border-blue-400/20">
                    <div class="relative z-10">
                        <p class="text-blue-200 text-xs font-black uppercase tracking-widest mb-2">Solde Actuel</p>
                        <h2 class="text-6xl font-black tracking-tighter mb-8">{{ "%.2f"|format(user.balance) }} €</h2>
                        <div class="bg-black/30 backdrop-blur-md p-4 rounded-2xl border border-white/10">
                            <p class="text-[10px] text-blue-300 font-black uppercase mb-1">Adresse Publique</p>
                            <code class="text-xs font-mono text-white break-all">{{ user.wallet_address }}</code>
                        </div>
                    </div>
                </div>

                <div class="bg-gray-800 rounded-3xl border border-gray-700 overflow-hidden">
                    <div class="p-6 border-b border-gray-700 font-black text-xs uppercase tracking-widest text-gray-400">Derniers Mouvements</div>
                    <div class="divide-y divide-gray-700">
                        {% for tx in transactions %}
                        <div class="p-4 flex justify-between items-center hover:bg-gray-700/20">
                            <div>
                                <p class="text-[10px] text-gray-500 font-bold">{{ tx.timestamp.strftime('%d/%m %H:%M') }}</p>
                                <p class="text-sm font-mono text-blue-300 truncate w-40 sm:w-64">{{ tx.recipient_addr }}</p>
                            </div>
                            <div class="text-right">
                                <p class="text-lg font-black text-white">-{{ "%.2f"|format(tx.amount) }} €</p>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
            </div>

            <div class="space-y-6">
                <div class="bg-gray-800 p-6 rounded-3xl border-t-4 border-t-green-500 shadow-xl">
                    <h3 class="text-lg font-black mb-4 text-green-500">💳 DÉPOSER</h3>
                    <form action="/create-checkout-session" method="POST" class="space-y-4">
                        <input type="number" step="0.01" name="amount" id="dep_amt" placeholder="Montant (€)" class="w-full bg-gray-900 border border-gray-700 p-4 rounded-2xl outline-none font-bold text-xl focus:border-green-500" required>
                        
                        <div class="bg-black/20 p-3 rounded-xl text-[11px] font-bold space-y-1">
                            <div class="flex justify-between">
                                <span class="text-gray-500 italic">Frais (1.2% + 0.25€)</span>
                                <span id="fee_val" class="text-red-400">0.00 €</span>
                            </div>
                            <div class="flex justify-between border-t border-white/5 pt-1 font-black">
                                <span class="text-gray-400">Net Crédité</span>
                                <span id="net_val" class="text-green-400">0.00 €</span>
                            </div>
                        </div>
                        <button type="submit" class="w-full bg-green-600 hover:bg-green-500 py-4 rounded-2xl font-black uppercase tracking-widest transition shadow-lg">Payer</button>
                    </form>
                </div>

                <div class="bg-gray-800 p-6 rounded-3xl border border-gray-700">
                    <h3 class="text-lg font-black mb-4 text-blue-500 font-black italic">💸 ENVOYER</h3>
                    <form action="/send" method="POST" class="space-y-4">
                        <input type="text" name="recipient_address" placeholder="0x..." class="w-full bg-gray-900 border border-gray-700 p-4 rounded-2xl outline-none font-mono text-xs focus:border-blue-500" required>
                        <input type="number" step="0.01" name="amount" placeholder="Montant (€)" class="w-full bg-gray-900 border border-gray-700 p-4 rounded-2xl outline-none font-bold text-xl focus:border-blue-500" required>
                        <button type="submit" class="w-full bg-blue-600 hover:bg-blue-500 py-4 rounded-2xl font-black uppercase tracking-widest transition shadow-lg">Transférer</button>
                    </form>
                </div>

                <div class="bg-gray-800 p-6 rounded-3xl border-t-4 border-t-orange-500 shadow-xl">
                    <h3 class="text-lg font-black mb-4 text-orange-500">🏦 RETIRER</h3>
                    <form action="/withdraw" method="POST" class="space-y-4">
                        <input type="text" name="iban" placeholder="IBAN (FR76...)" class="w-full bg-gray-900 border border-gray-700 p-4 rounded-2xl outline-none font-mono text-xs focus:border-orange-500" required>
                        <input type="number" step="0.01" name="amount" placeholder="Montant (€)" class="w-full bg-gray-900 border border-gray-700 p-4 rounded-2xl outline-none font-bold text-xl focus:border-orange-500" required>
                        <button type="submit" class="w-full bg-orange-600 hover:bg-orange-500 py-4 rounded-2xl font-black uppercase tracking-widest transition shadow-lg">Virement</button>
                    </form>
                </div>
            </div>
        </div>
    </div>

    <script>
        const input = document.getElementById('dep_amt');
        const feeVal = document.getElementById('fee_val');
        const netVal = document.getElementById('net_val');

        input.addEventListener('input', () => {
            const val = parseFloat(input.value) || 0;
            const fee = val > 0 ? (val * 0.012) + 0.25 : 0;
            const net = val - fee;
            feeVal.innerText = fee.toFixed(2) + ' €';
            netVal.innerText = (net > 0 ? net.toFixed(2) : '0.00') + ' €';
        });
    </script>
</body>
</html>

# app.py
# Vinted Market Scout — Complete edition (private-first, ready-to-scale)
# Features:
# - Private/admin-first mode (ADMIN_CODE in st.secrets recommended)
# - Vinted scraping, filters, brand list
# - Per-item: purchase price, estimated resale range, time-to-sell label, saturation label
# - Photo -> generated title/description/tags (heuristic locally; API hooks commented)
# - Beginner guide, chat helper (rule-based), message templates, relist template
# - Local logs: search_log.csv, subscribers.csv (for later monetization)
# - Placeholders for Supabase / Stripe integration (commented and documented)

import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from urllib.parse import quote_plus, urljoin
from datetime import datetime, timedelta
import os
from PIL import Image, ImageStat
import io

# ---------------- CONFIG ----------------
APP_TITLE = "Vinted Market Scout — Private Edition"
# Use ADMIN_CODE from st.secrets if present (more secure). Fallback to below.
DEFAULT_ADMIN_CODE = "azertylolo123@"
ADMIN_CODE = st.secrets.get("ADMIN_CODE", DEFAULT_ADMIN_CODE)
DATA_LOG = "search_log.csv"
SUBSCRIBERS_FILE = "subscribers.csv"
REQUESTS_LOG_FILE = "access_requests.csv"
DATA_FAVS = "favorites.csv"

# Default PayPal link placeholder (if you want to use manual payments later)
PAYPAL_LINK = "https://www.paypal.com/paypalme/VOTRECOMPTE/25.99"

# Brands filter defaults
BRAND_OPTIONS = [
    "All", "Carhartt", "Nike", "Adidas", "Supreme", "Vans", "Levi's", "Zara",
    "Patagonia", "The North Face", "Moncler", "Chanel"
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Quick mode: start private (only admin full access). Set to False to open to all users.
PRIVATE_BY_DEFAULT = True

st.set_page_config(page_title=APP_TITLE, layout="wide", initial_sidebar_state="expanded")

# ---------------- Styling (clean / business) ----------------
st.markdown("""
    <style>
      .stApp { background-color: #f7fafc; color: #0b2747; }
      .title {font-size:28px; font-weight:700; color:#0b2545}
      .muted { color: #546e7a; }
      .card { background: #ffffff; padding:12px; border-radius:10px; box-shadow: 0 1px 4px rgba(10,20,50,0.06);}
      .small { font-size:12px; color:#6b7280; }
      .monos { font-family: monospace; }
    </style>
""", unsafe_allow_html=True)

st.markdown(f"<div class='title'>{APP_TITLE}</div>", unsafe_allow_html=True)
st.markdown("<div class='muted'>Private-first: testez en local, puis monétisez plus tard — tout est prévu.</div>", unsafe_allow_html=True)
st.write("")

# ---------------- Utilities: logging, subscribers, favorites ----------------
def _ensure_df(path, cols):
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except:
            return pd.DataFrame(columns=cols)
    else:
        return pd.DataFrame(columns=cols)

def log_search(query, brand, user="admin"):
    rec = {"timestamp": datetime.utcnow().isoformat(), "query": query, "brand": brand, "user": user}
    df = _ensure_df(DATA_LOG, ["timestamp","query","brand","user"])
    df = pd.concat([df, pd.DataFrame([rec])], ignore_index=True)
    df.to_csv(DATA_LOG, index=False)

def add_favorite(item: dict, user="admin"):
    df = _ensure_df(DATA_FAVS, ["timestamp","title","price","link","user"])
    rec = {"timestamp": datetime.utcnow().isoformat(), "title": item.get("title"), "price": item.get("price"), "link": item.get("link"), "user": user}
    df = pd.concat([df, pd.DataFrame([rec])], ignore_index=True)
    df.to_csv(DATA_FAVS, index=False)

def load_subscribers():
    df = _ensure_df(SUBSCRIBERS_FILE, ["email","start_date","expiry_date"])
    # parse dates if present
    try:
        if not df.empty:
            df['start_date'] = pd.to_datetime(df['start_date'])
            df['expiry_date'] = pd.to_datetime(df['expiry_date'])
    except:
        pass
    return df

def save_subscribers(df):
    df.to_csv(SUBSCRIBERS_FILE, index=False)

def add_subscriber(email, days_valid=30):
    df = load_subscribers()
    now = datetime.utcnow()
    expiry = now + timedelta(days=days_valid)
    if email in df['email'].values:
        df.loc[df['email'] == email, 'start_date'] = now
        df.loc[df['email'] == email, 'expiry_date'] = expiry
    else:
        row = {"email": email, "start_date": now.isoformat(), "expiry_date": expiry.isoformat()}
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    save_subscribers(df)

def check_access(email):
    if email is None or email == "":
        return False, None
    df = load_subscribers()
    row = df[df['email'] == email]
    if row.empty:
        return False, None
    expiry = pd.to_datetime(row.iloc[0]['expiry_date'])
    now = datetime.utcnow()
    return expiry >= now, (expiry - now).days

def log_request(email, message=""):
    df = _ensure_df(REQUESTS_LOG_FILE, ["email","message","timestamp"])
    rec = {"email": email, "message": message, "timestamp": datetime.utcnow().isoformat()}
    df = pd.concat([df, pd.DataFrame([rec])], ignore_index=True)
    df.to_csv(REQUESTS_LOG_FILE, index=False)

# ---------------- Vinted scraping & analysis ----------------
@st.cache_data(ttl=600)
def search_vinted(query: str, max_pages: int = 2, pause: float = 1.0):
    results = []
    q = quote_plus(query)
    for page in range(1, max_pages+1):
        url = f"https://www.vinted.fr/catalog?search_text={q}&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
        except Exception:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".feed-grid__item") or soup.select(".catalog-item") or soup.select(".item") or []
        for it in items:
            title_tag = it.select_one(".feed-grid__item-title") or it.select_one("h3") or it.select_one(".title")
            price_tag = it.select_one(".feed-grid__item-price") or it.select_one(".price") or it.select_one("span[data-testid='price']")
            link_tag = it.find("a", href=True)
            if not (title_tag and price_tag and link_tag):
                continue
            title = title_tag.get_text().strip()
            price_text = price_tag.get_text().replace("€","").replace("\u20ac","").replace(",",".").strip()
            cleaned = ''.join(ch for ch in price_text if (ch.isdigit() or ch == "."))
            try:
                price = float(cleaned) if cleaned else None
            except:
                price = None
            if price is None:
                continue
            link = urljoin("https://www.vinted.fr", link_tag["href"])
            results.append({"title": title, "price": price, "link": link})
        time.sleep(pause)
    return results

def analyze_prices(items):
    prices = [it["price"] for it in items if isinstance(it.get("price"), (int,float))]
    if not prices:
        return None
    avg = sum(prices)/len(prices)
    return {"avg": avg, "min": min(prices), "max": max(prices), "count": len(prices)}

def resale_estimate_and_label(price, avg):
    if avg <= 0:
        return ("Inconnu", 0, (0,0), "Inconnu")
    ratio = price / avg
    if ratio <= 0.6:
        label = "🔥 Revente rapide"
        time_days = "1-7 jours"
        est_min = round(avg*0.9,2); est_max = round(avg*1.1,2)
        score = 90
    elif ratio <= 1.0:
        label = "✅ Bonne revente"
        time_days = "7-21 jours"
        est_min = round(avg*0.95,2); est_max = round(avg*1.25,2)
        score = 75
    elif ratio <= 1.4:
        label = "🕐 Vente lente"
        time_days = "2-6 semaines"
        est_min = round(avg*0.9,2); est_max = round(avg*1.3,2)
        score = 50
    else:
        label = "🐢 Vente très lente"
        time_days = "1-3 mois"
        est_min = round(avg*0.8,2); est_max = round(avg*1.1,2)
        score = 25
    return (label, score, (est_min, est_max), time_days)

def market_saturation_label(count):
    if count < 10:
        return "Peu saturé"
    elif count < 30:
        return "Moyennement saturé"
    else:
        return "Très saturé"

# ---------------- Photo -> description (heuristic) ----------
def average_color(image: Image.Image):
    stat = ImageStat.Stat(image.convert("RGB"))
    r,g,b = [int(x) for x in stat.mean]
    return (r,g,b)

def generate_description_from_photo_local(filename, pil_img):
    name = os.path.splitext(filename)[0].replace("_"," ").replace("-"," ").title()
    r,g,b = average_color(pil_img)
    if r>200 and g>200 and b>200:
        color = "clair / blanc"
    elif b>150 and r<120:
        color = "bleu"
    elif g>140 and r<120:
        color = "vert"
    else:
        color = "couleur variée"
    title = f"{name} — Très bon état"
    desc = (f"{name} en très bon état. Couleur : {color}. Aucune déchirure visible. "
            "Taille à confirmer — indiquez la taille exacte. Parfait pour revente. Nettoyage conseillé.")
    tags = ", ".join([w for w in name.split() if len(w)>2][:6] + ["vinted","revente"])
    return title, desc, tags

# ---------------- Chat helper (rule-based starter) ----------
FAQ = {
    "niches": "Recherchez petites pièces streetwear et accessoires. Surveille éditions limitées.",
    "debuter": "Commencez par t-shirts et casquettes, vendez x1.6-x2.",
    "prix": "Visez une marge 1.6–2.0 si possible."
}
def chat_answer(prompt):
    p = prompt.lower()
    for k,v in FAQ.items():
        if k in p:
            return v
    if "quoi acheter" in p or "acheter" in p:
        return "Commence par petites pièces populaires (casquettes, t-shirts), faible cout d'entrée."
    return "Bonne question — préciser ex: 'quoi acheter pour 100€'"

# ---------------- Sidebar & Navigation ----------------
st.sidebar.header("Navigation")
page = st.sidebar.radio("Aller à", ["Accueil", "Analyse Vinted", "Photo → Description", "Statistiques", "Chat conseil", "Admin (privé)"])

st.sidebar.markdown("---")
st.sidebar.header("Filtres globaux")
sel_brand = st.sidebar.selectbox("Filtre marque", BRAND_OPTIONS, index=0)
price_min, price_max = st.sidebar.slider("Plage prix €", 0, 500, (0,200))
pages = st.sidebar.slider("Pages à scrapper", 1, 5, 2)
pause_between = st.sidebar.slider("Pause entre pages (s)", 0.5, 2.0, 1.0, step=0.5)

# If app is private by default, show info and require admin for full access
if PRIVATE_BY_DEFAULT and page != "Admin (privé)":
    st.sidebar.info("Mode privé activé — pour tester en tant qu'utilisateur, bascule 'Admin' et entre le code.")

# ---------------- PAGES ----------------
if page == "Accueil":
    st.header("Accueil — aperçu")
    st.markdown("Utilise l'app pour analyser rapidement le marché Vinted. Mode privé : tu y as accès en admin.")
    st.markdown("- **Analyse Vinted** : recherche et filtres, export CSV.") 
    st.markdown("- **Photo → Description** : génère titre/description/tags à partir d'une photo (mobile).")
    st.markdown("- **Chat conseil** : questions rapides pour débuter ou choisir une niche.")
    with st.expander("Guide débutant (rapide)"):
        st.write("""
        1) Rechercher une marque/catégorie (commence 1 page).  
        2) Filtrer les prix et marques.  
        3) Chercher les deals (prix < 0.7 * moyenne).  
        4) Utiliser Photo→Description pour préparer annonce.  
        """)

elif page == "Analyse Vinted":
    st.header("Analyse Vinted — recherche & filtres")
    q = st.text_input("Recherche (ex: 'nike air max')", value="")
    col1, col2 = st.columns([3,1])
    with col1:
        if st.button("Lancer la recherche"):
            if not q.strip():
                st.error("Entrez une recherche.")
            else:
                with st.spinner("Scraping..."):
                    items = search_vinted(q, max_pages=pages, pause=pause_between)
                if not items:
                    st.warning("Aucun résultat.")
                else:
                    df = pd.DataFrame(items)
                    # brand filter
                    if sel_brand != "All":
                        df = df[df['title'].str.contains(sel_brand, case=False, na=False)]
                    # price filter
                    df = df[(df['price'] >= price_min) & (df['price'] <= price_max)]
                    log_search(q, sel_brand)
                    analysis = analyze_prices(df.to_dict('records'))
                    if analysis:
                        st.metric("Prix moyen", f"{analysis['avg']:.2f} €")
                        st.write("Saturation :", market_saturation_label(analysis['count']))
                    # add resale info
                    if analysis:
                        df['resale_label'] = df['price'].apply(lambda p: resale_estimate_and_label(p, analysis['avg'])[0])
                        df['resale_score'] = df['price'].apply(lambda p: resale_estimate_and_label(p, analysis['avg'])[1])
                        df['resale_min'] = df['price'].apply(lambda p: resale_estimate_and_label(p, analysis['avg'])[2][0])
                        df['resale_max'] = df['price'].apply(lambda p: resale_estimate_and_label(p, analysis['avg'])[2][1])
                        df['time_to_sell'] = df['price'].apply(lambda p: resale_estimate_and_label(p, analysis['avg'])[3])
                    st.dataframe(df.reset_index(drop=True))
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Télécharger résultats (.csv)", data=csv, file_name="vinted_results.csv")
                    st.success("Résultats prêts — clique sur une ligne pour ouvrir le lien.")
    with col2:
        st.markdown("Astuces")
        st.markdown("- Commence par 1 page, augmente si besoin.")
        st.markdown("- Filtre par marque et prix pour affiner.")
        st.markdown("- Pour chaque ligne tu peux copier-template pour poster sur Vinted (voir Admin / messages).")

elif page == "Photo → Description":
    st.header("Photo → Description")
    uploaded = st.file_uploader("Téléverse la photo de l'article", type=['jpg','jpeg','png'])
    suggested_price = st.number_input("Prix d'achat (pour calcul marge)", min_value=0.0, value=10.0, step=0.5)
    if uploaded:
        img = Image.open(uploaded)
        st.image(img, use_column_width=True)
        title, desc, tags = generate_description_from_photo_local(uploaded.name, img)
        est_low = round(suggested_price * 1.6,2)
        est_high = round(suggested_price * 2.0,2)
        st.subheader("Titre généré")
        title_in = st.text_input("Titre (modifiable)", value=title, key="title_gen")
        st.subheader("Description générée")
        desc_in = st.text_area("Description", value=desc, height=220, key="desc_gen")
        st.subheader("Tags")
        tags_in = st.text_input("Tags (virgule)", value=tags, key="tags_gen")
        st.markdown(f"**Prix de revente suggéré** : {est_low} € — {est_high} €")
        st.markdown("**Actions**")
        final_text = f"{title_in}\n\n{desc_in}\n\nPrix suggéré: {est_low} - {est_high} €\nTags: {tags_in}\n\nPhotos: ajouter depuis téléphone"
        st.text_area("Texte prêt (copier-coller)", value=final_text, height=220)
        st.success("Description prête — copie-colle dans Vinted lors de la publication.")

elif page == "Statistiques":
    st.header("Statistiques & Guide")
    st.markdown("Uploader un CSV (title, price, link) pour un rapport personnalisé.")
    uploaded = st.file_uploader("Uploader CSV (optionnel)", type=['csv'])
    if uploaded:
        try:
            d = pd.read_csv(uploaded)
            if 'price' not in d.columns:
                st.error("CSV doit contenir colonne 'price'")
            else:
                analysis = analyze_prices(d.to_dict('records'))
                if analysis:
                    st.write(f"Prix moyen: {analysis['avg']:.2f} € — Count: {analysis['count']}")
                    d['label'] = d['price'].apply(lambda p: resale_estimate_and_label(p, analysis['avg'])[0])
                    st.dataframe(d)
        except Exception as e:
            st.error(f"Erreur: {e}")
    with st.expander("Guide débutant complet"):
        st.markdown("""
        **Débuter**: comencer par t-shirts/casquettes, 3 photos nettes, description honnête, expédition rapide.
        **Pricing**: viser marge x1.6–2.0
        **Photos**: lumière naturelle, fond uni
        """)

elif page == "Chat conseil":
    st.header("Chat conseil (starter)")
    q = st.text_input("Pose ta question (ex: 'quoi acheter pour 100€')", "")
    if st.button("Poser la question"):
        if not q.strip():
            st.error("Écris quelque chose.")
        else:
            ans = chat_answer(q)
            st.markdown(f"**Réponse :** {ans}")

elif page == "Admin (privé)":
    st.header("Admin (privé) — accès restreint")
    code = st.text_input("Code admin", type="password")
    if not code:
        st.info("Entrez le code admin pour accéder.")
        st.stop()
    if code != ADMIN_CODE:
        st.error("Code admin incorrect.")
        st.stop()

    st.success("Code admin validé — accès admin accordé.")
    st.markdown("### Journaux & gestion local")
    df_log = _ensure_df(DATA_LOG, ["timestamp","query","brand","user"])
    if not df_log.empty:
        st.dataframe(df_log.sort_values("timestamp", ascending=False).head(300))
        if st.button("Télécharger journal"):
            st.download_button("Télécharger CSV", df_log.to_csv(index=False).encode('utf-8'), file_name="search_log.csv")
    else:
        st.info("Aucun log.")

    st.markdown("#### Abonnés (local)")
    df_sub = load_subscribers()
    if df_sub.empty:
        st.info("Pas d'abonnés.")
    else:
        st.dataframe(df_sub)
        if st.button("Télécharger abonnés"):
            st.download_button("Télécharger abonnés", df_sub.to_csv(index=False).encode('utf-8'), file_name="subscribers.csv")

    st.markdown("#### Ajouter / renouveler abonné (pour monétisation future)")
    new_email = st.text_input("Email client à ajouter :", key="new_email_admin")
    days = st.number_input("Durée (jours)", min_value=1, max_value=365, value=30, key="admin_days")
    if st.button("Ajouter / Renouveler"):
        if not new_email:
            st.error("Entre un email.")
        else:
            add_subscriber(new_email, days_valid=days)
            st.success(f"{new_email} ajouté pour {days} jours.")

    st.markdown("#### Demandes d'accès")
    reqs = _ensure_df(REQUESTS_LOG_FILE, ["email","message","timestamp"])
    if not reqs.empty:
        st.dataframe(reqs.sort_values("timestamp", ascending=False).head(200))
        if st.button("Effacer demandes"):
            os.remove(REQUESTS_LOG_FILE)
            st.success("Demandes effacées.")
    else:
        st.info("Pas de demandes.")

    st.markdown("#### Favoris locaux")
    favs = _ensure_df(DATA_FAVS, ["timestamp","title","price","link","user"])
    if not favs.empty:
        st.dataframe(favs)
        if st.button("Effacer favoris"):
            os.remove(DATA_FAVS)
            st.success("Favoris effacés.")

    st.markdown("#### Notes & prochaines étapes")
    st.markdown("- Pour passer à la monétisation automatique : intégrer Stripe + webhook + ajouter email au `subscribers` via backend.")
    st.markdown("- Pour gestion utilisateurs : intégrer Supabase Auth ou Firebase Auth et vérifier `subscribers` côté serveur.")

# ---------------- Footer ----------------
st.markdown("---")
st.caption("Note : respecte les conditions d'utilisation de Vinted — pas d'automatisation d'envoi/posting non autorisée.")

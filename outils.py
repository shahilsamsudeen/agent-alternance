import unicodedata
from datetime import date
from pathlib import Path

from google.genai import types
from openpyxl import load_workbook
from pypdf import PdfReader

import os
import time
import requests

import html

# ---------- Outil 1 : lire une offre ----------
def lire_offre(chemin: str) -> dict:
    fichier = Path(chemin)
    if not fichier.exists():
        return {"erreur": f"Fichier introuvable : {chemin}"}
    return {"texte": fichier.read_text(encoding="utf-8")}



# ---------- Outil 2 : lire le CV ----------
def lire_cv(chemin: str) -> dict:
    fichier = Path(chemin)
    if not fichier.exists():
        return {"erreur": f"CV introuvable : {chemin}"}

    if fichier.suffix.lower() == ".pdf":
        pages = PdfReader(fichier).pages
        texte = '\n'.join(page.extract_text() or "" for page in pages)
    else: 
        texte = fichier.read_text(encoding="utf-8")

    if not texte.strip():
        return {"erreur": "CV illisible : c'est sûrement un PDF image. Exporte-le en PDF texte depuis Word ou Canva."}
    return {"texte": texte}



# ---------- Réglages du tableau ----------
TABLEAU = Path("Tableau_de_bord_Bachelor2.xlsx")
ONGLET = "Candidatures"
LIGNE_ENTETE = 7   # ligne où se trouvent tes titres de colonnes
CLES = ["date", "methode", "lien", "poste", "entreprise", "statut",
        "relance", "contact", "entretien", "resultat", "commentaire"]


def _normaliser(texte) -> str:
    """'Méthode ' -> 'methode' : minuscules, sans accents, sans espaces autour."""
    texte = unicodedata.normalize("NFKD", str(texte))
    return "".join(c for c in texte if not unicodedata.combining(c)).lower().strip()


def _ouvrir_tableau():
    """Ouvre le tableau et repère le numéro de chaque colonne grâce aux en-têtes."""
    if not TABLEAU.exists():
        raise FileNotFoundError(f"Tableau introuvable : {TABLEAU}")
    classeur = load_workbook(TABLEAU)
    if ONGLET not in classeur.sheetnames:
        raise ValueError(f"Onglet '{ONGLET}' introuvable. Onglets présents : {classeur.sheetnames}")
    feuille = classeur[ONGLET]

    colonnes = {}
    for cellule in feuille[LIGNE_ENTETE]:
        if cellule.value is None:
            continue
        entete = _normaliser(cellule.value)
        for cle in CLES:
            if cle in entete and cle not in colonnes:
                colonnes[cle] = cellule.column
    if "entreprise" not in colonnes:
        raise ValueError("Aucune colonne 'Entreprise' trouvée dans la ligne d'en-tête.")
    return classeur, feuille, colonnes


def _derniere_ligne(feuille, colonnes) -> int:
    """Dernière ligne où la colonne Entreprise est remplie."""
    col = colonnes["entreprise"]
    for ligne in range(feuille.max_row, LIGNE_ENTETE, -1):
        if feuille.cell(ligne, col).value not in (None, ""):
            return ligne
    return LIGNE_ENTETE


# ---------- Outil 3 : vérifier les doublons ----------
def verifier_doublon(entreprise: str) -> dict:
    _, feuille, colonnes = _ouvrir_tableau()
    cherche = _normaliser(entreprise)
    trouvees = []
    for ligne in range(LIGNE_ENTETE + 1, _derniere_ligne(feuille, colonnes) + 1):
        valeur = feuille.cell(ligne, colonnes["entreprise"]).value
        if valeur and cherche in _normaliser(valeur):
            poste = feuille.cell(ligne, colonnes["poste"]).value if "poste" in colonnes else None
            statut = feuille.cell(ligne, colonnes["statut"]).value if "statut" in colonnes else None
            trouvees.append({"ligne": ligne, "entreprise": valeur, "poste": poste, "statut": statut})
    return {"deja_dans_le_tableau": len(trouvees) > 0, "candidatures": trouvees}


# ---------- Outil 4 : ajouter une ligne ----------
def ajouter_au_tableau(poste: str, entreprise: str, lien: str, score: int,
                       competences: str, commentaire: str = "", methode: str = "Agent IA") -> dict:
    classeur, feuille, colonnes = _ouvrir_tableau()
    ligne = _derniere_ligne(feuille, colonnes) + 1
    valeurs = {
        "date": date.today(),
        "methode": methode,
        "lien": lien,
        "poste": poste,
        "entreprise": entreprise,
        "statut": "À postuler",
        "commentaire": f"Score {int(score)}/10 | Compétences : {competences} | {commentaire}",
    }
    for cle, valeur in valeurs.items():
        if cle in colonnes:
            cellule = feuille.cell(ligne, colonnes[cle], valeur)
            if cle == "date":
                cellule.number_format = "DD/MM/YYYY"
    try:
        classeur.save(TABLEAU)
    except PermissionError:
        return {"erreur": "Impossible d'enregistrer : ferme le tableau dans Excel et relance."}
    return {"ok": True, "ligne": ligne}



# ---------- Outil 5 : chercher des offres sur La bonne alternance ----------
URL_LBA = "https://api.apprentissage.beta.gouv.fr/api/job/v1/search"
BONDY = {"latitude": 48.9022, "longitude": 2.4828}


def _lire(donnees, chemin, defaut="non précisé"):
    """Lit une valeur imbriquée : _lire(offre, 'workplace.name') = offre['workplace']['name']."""
    for cle in chemin.split("."):
        if not isinstance(donnees, dict) or donnees.get(cle) in (None, ""):
            return defaut
        donnees = donnees[cle]
    return donnees


def chercher_offres(codes_rome: str, rayon_km: int = 60, nombre: int = 10) -> dict:
    """Cherche des offres en alternance autour de Bondy sur La bonne alternance."""
    jeton = os.environ.get("LBA_API_TOKEN")
    if not jeton:
        return {"erreur": "LBA_API_TOKEN manquant dans le fichier .env."}
    try:
        reponse = requests.get(
            URL_LBA,
            headers={"Authorization": f"Bearer {jeton}", "Accept": "application/json"},
            params={"romes": codes_rome.replace(" ", ""), "radius": int(rayon_km), **BONDY},
            timeout=30,
        )
    except requests.RequestException as e:
        return {"erreur": f"Connexion impossible : {type(e).__name__}"}

    if reponse.status_code != 200:
        return {"erreur": f"Recherche refusée (code {reponse.status_code}) : {reponse.text[:300]}"}

    offres = []
    deja_vues = set()
    for o in reponse.json().get("jobs", []):
        titre = html.unescape(str(_lire(o, "offer.title")))
        entreprise = _lire(o, "workplace.name")
        cle = (titre.lower(), str(entreprise).lower())
        if cle in deja_vues:
            continue          # même poste dans la même entreprise : on l'ignore
        deja_vues.add(cle)

        offres.append({
            "id": _lire(o, "identifier.id"),
            "titre": titre,
            "entreprise": entreprise,
            "lieu": _lire(o, "workplace.location.address"),
            "contrat": ", ".join(_lire(o, "contract.type", [])) or "non précisé",
            "debut": _lire(o, "contract.start"),
            "lien": _lire(o, "apply.url"),
            "description": str(_lire(o, "offer.description", ""))[:1500],
        })
        if len(offres) >= max(1, min(int(nombre), 20)):
            break
    return {"nombre": len(offres), "offres": offres}



# ---------- Les fiches que le LLM lit ----------
DECLARATIONS = [
    types.FunctionDeclaration(
        name="lire_offre",
        description="Lit une offre d'alternance enregistrée dans un fichier texte.",
        parameters={
            "type": "OBJECT",
            "properties": {"chemin": {"type": "STRING", "description": "Chemin du fichier de l'offre"}}, "required": ["chemin"],
        },
    ),
    types.FunctionDeclaration(
        name="lire_cv",
        description="Lit le CV de l'étudiant (PDF ou texte) pour connaître ses compétences et sa formation.",
        parameters={
            "type": "OBJECT",
            "properties": {"chemin": {"type": "STRING", "description": "Chemin du fichier CV"}},
            "required": ["chemin"],
        },
    ),
        types.FunctionDeclaration(
        name="verifier_doublon",
        description="Vérifie si l'étudiant a déjà une candidature pour cette entreprise dans son tableau de suivi. "
                    "À appeler avant tout ajout.",
        parameters={
            "type": "OBJECT",
            "properties": {"entreprise": {"type": "STRING", "description": "Nom de l'entreprise"}},
            "required": ["entreprise"],
        },
    ),
    types.FunctionDeclaration(
        name="ajouter_au_tableau",
        description="Ajoute une offre analysée dans le tableau Excel de suivi des candidatures.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "poste": {"type": "STRING"},
                "entreprise": {"type": "STRING"},
                "lien": {"type": "STRING", "description": "URL de l'offre ou nom du fichier"},
                "score": {"type": "INTEGER", "description": "Score de 0 à 10"},
                "competences": {"type": "STRING", "description": "Compétences clés demandées, séparées par des virgules"},
                "commentaire": {"type": "STRING", "description": "Une phrase : point fort ou point bloquant"},
                "methode": {"type": "STRING", "description": "Où l'offre a été trouvée : Indeed, WTTJ, site de l'entreprise..."},
            },
            "required": ["poste", "entreprise", "lien", "score", "competences"],
        },
    ),
        types.FunctionDeclaration(
        name="chercher_offres",
        description="Cherche des offres en alternance actives autour de Bondy sur La bonne alternance, "
                    "à partir de codes métiers ROME. Codes utiles en informatique : "
                    "M1805 études et développement informatique, M1810 production et exploitation de SI, "
                    "M1801 administration de systèmes d'information, M1802 expertise et support en SI, "
                    "I1401 maintenance informatique et bureautique.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "codes_rome": {"type": "STRING", "description": "Un ou plusieurs codes ROME séparés par des virgules, ex. 'M1810,M1801'"},
                "rayon_km": {"type": "INTEGER", "description": "Rayon de recherche autour de Bondy en km : 10, 30, 60 ou 100"},
                "nombre": {"type": "INTEGER", "description": "Nombre d'offres à récupérer, de 1 à 20"},
            },
            "required": ["codes_rome"],
        },
    ),
] 


# ---------- Le lien entre le nom vu par le LLM et la vraie fonction ----------
OUTILS = {
    "lire_offre": lire_offre,
    "lire_cv": lire_cv,
    "verifier_doublon": verifier_doublon,
    "ajouter_au_tableau": ajouter_au_tableau,
    "chercher_offres": chercher_offres,
}
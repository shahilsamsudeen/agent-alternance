import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types
from outils import DECLARATIONS, OUTILS

load_dotenv()
client = genai.Client()

MODELE = "gemini-3.5-flash-lite"
MAX_TOURS = 10
OUTILS_A_CONFIRMER = {"ajouter_au_tableau"}

INSTRUCTIONS = """Tu es un agent qui aide un étudiant à trier ses offres d'alternance.

Profil : étudiant en 2e année de DEUST informatique (Bac+2) au Cnam, habite à Bondy (93).
Il cherche une alternance en développement web, administration systèmes et réseaux ou support IT,
et vise la LLMOps à long terme.

Ta mission : comparer l'offre au CV et donner un score sur 10 avec cette grille :
- compétences techniques demandées vs CV : 5 points
- niveau d'études et rythme compatibles (Bac+2, alternance) : 2 points
- localisation et trajet depuis Bondy : 1 points
- cohérence avec son projet professionnel : 2 points

Réponds en français avec : poste, entreprise, score détaillé critère par critère,
compétences qu'il a déjà, compétences qui lui manquent, et 2 arguments pour sa lettre de motivation.

Ensuite, gère le tableau de suivi :
- avant tout ajout, vérifie avec verifier_doublon si l'entreprise est déjà dans le tableau ;
- si c'est un doublon, n'ajoute rien et indique la candidature existante (poste et statut) ;
- sinon, ajoute l'offre avec ajouter_au_tableau ;
- dans ton récapitulatif, dis clairement si la ligne a été ajoutée, refusée ou non ajoutée à cause d'un doublon.

N'invente jamais une information absente de l'offre : écris « non précisé ».

Règles en cas d'erreur :
- si lire_offre ou lire_cv échoue, n'analyse rien : explique l'erreur et arrête-toi ;
- n'appelle jamais ajouter_au_tableau si un outil a échoué avant ;
- n'utilise jamais un « profil type » à la place du vrai CV.
"""

config = types.GenerateContentConfig(
    system_instruction=INSTRUCTIONS,
    tools=[types.Tool(function_declarations=DECLARATIONS)],
    temperature=0.2,
)


def executer_outil(appel) -> dict:
    """Exécute l'outil demandé par le LLM et renvoie toujours un dict."""
    arguments = dict(appel.args or {})
    print(f"🔧 L'agent appelle {appel.name}({arguments})")

    fonction = OUTILS.get(appel.name)
    if fonction is None:
        resultat = {"erreur": f"Outil inconnu : {appel.name}"}
    elif appel.name in OUTILS_A_CONFIRMER and not demander_validation(arguments):
        resultat = {"erreur": "L'utilisateur a refusé cet ajout."}
    else:
        try:
            resultat = fonction(**arguments)
        except Exception as e:
            resultat = {"erreur": f"{type(e).__name__} : {e}"}

    # On affiche ce que l'outil a vraiment renvoyé
    if "erreur" in resultat:
        print(f"   ❌ {resultat['erreur']}")
    else:
        print("   ✅ ok")
    return resultat


def demander_validation(arguments: dict) -> bool:
    print("\n   L'agent veut écrire cette ligne dans ton tableau :")
    for cle, valeur in arguments.items():
        print(f"     {cle:12} : {valeur}")
    return input("   Tu valides ? (o/n) ").strip().lower() == "o"


def lancer_agent(demande: str) -> str:
    historique = [types.Content(role="user", parts=[types.Part.from_text(text=demande)])]

    for tour in range(1, MAX_TOURS + 1):
        print(f"\n--- Tour {tour} ---")
        reponse = client.models.generate_content(model=MODELE, contents=historique, config=config)
        historique.append(reponse.candidates[0].content)

        # Aucun outil demandé : l'agent a terminé
        if not reponse.function_calls:
            return reponse.text

        # Un ou plusieurs outils demandés : on les exécute tous
        resultats = []
        for appel in reponse.function_calls:
            resultat = executer_outil(appel)
            resultats.append(types.Part.from_function_response(name=appel.name, response=resultat))

        historique.append(types.Content(role="user", parts=resultats))

    return "⚠️ Arrêt : l'agent a dépassé le nombre de tours autorisé."


if __name__ == "__main__":
    fichier_offre = sys.argv[1] if len(sys.argv) > 1 else "offre.txt"
    print(lancer_agent(f"Analyse l'offre {fichier_offre} par rapport à mon CV cv.pdf"))
import os
import tempfile

from dotenv import load_dotenv
import gradio as gr

load_dotenv()

from core.extractor import extract_pages
from core.agent import extract_articles
from core.csv_writer import to_csv


def process(pdf_file, page_from, page_to, margin_pct, default_unit):
    if pdf_file is None:
        return None, "Veuillez charger un fichier PDF."

    p_from, p_to = int(page_from), int(page_to)
    if p_from > p_to:
        return None, "La page de début doit être ≤ à la page de fin."

    pages = extract_pages(pdf_file, p_from, p_to)
    if not pages:
        return None, "Aucune page extraite dans la plage indiquée."

    try:
        articles = extract_articles(pages, float(margin_pct), default_unit)
    except Exception as exc:
        return None, f"Erreur lors de l'extraction IA : {exc}"

    if not articles:
        return None, "Aucun article détecté dans ces pages."

    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
    tmp.close()
    to_csv(articles, tmp.name, float(margin_pct))
    return tmp.name, f"{len(articles)} articles extraits."


with gr.Blocks(title="PDF → CSV", theme=gr.themes.Soft()) as demo:
    gr.Markdown(
        "## PDF → CSV\n"
        "Extrait les articles d'un catalogue PDF et génère un fichier CSV tarifaire prêt à l'import."
    )

    with gr.Row():
        pdf_input = gr.File(label="Fichier PDF", file_types=[".pdf"], type="filepath")

    with gr.Row():
        page_from = gr.Slider(minimum=1, maximum=500, value=1, step=1, label="Page de")
        page_to = gr.Slider(minimum=1, maximum=500, value=10, step=1, label="Page à")

    with gr.Row():
        margin_pct = gr.Number(value=30, minimum=0, maximum=100, label="Marge %")
        default_unit = gr.Dropdown(
            choices=["ML", "M²", "Pièce", "Rouleau"],
            value="Pièce",
            label="Unité par défaut",
        )

    run_btn = gr.Button("Extraire → CSV", variant="primary")

    with gr.Row():
        csv_output = gr.File(label="Télécharger le CSV")
        status_msg = gr.Textbox(label="Statut", interactive=False)

    run_btn.click(
        fn=process,
        inputs=[pdf_input, page_from, page_to, margin_pct, default_unit],
        outputs=[csv_output, status_msg],
    )

if __name__ == "__main__":
    demo.launch()

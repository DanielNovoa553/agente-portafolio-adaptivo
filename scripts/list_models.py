"""Lista los modelos de Gemini disponibles para tu API key.

Uso: python -m scripts.list_models [--all]
"""
import sys

from google import genai

from app.agent.models import list_models
from app.core.config import settings


def main() -> None:
    """Lista los modelos de Gemini disponibles para la API key.

    Sin `--all`, muestra los modelos ofrecidos por la interfaz y el modelo por
    defecto. Con `--all`, consulta directamente a Gemini e imprime todos los que
    admiten generateContent.

    Raises:
        SystemExit: Si falta GOOGLE_API_KEY en .env.
    """
    if not settings.google_api_key:
        sys.exit("Falta GOOGLE_API_KEY en .env")
    if "--all" in sys.argv:
        client = genai.Client(api_key=settings.google_api_key)
        for m in client.models.list():
            if "generateContent" in (m.supported_actions or []):
                print(m.name.removeprefix("models/"))
        return
    print(f"Modelos ofrecidos en la interfaz (por defecto: {settings.llm_model}):")
    for m in list_models(force=True):
        print(f"  {m['id']:<35} {m['name']}")


if __name__ == "__main__":
    main()

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Comprueba que el servicio está en marcha.

    Returns:
        dict[str, str]: Diccionario con el estado "ok".
    """
    return {"status": "ok"}

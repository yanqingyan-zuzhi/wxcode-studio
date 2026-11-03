"""Check that native generation interoperates with this container's toolchain."""
from pathlib import Path
import pytest

from app.errors import AppError
from app.generator import Generator
from app.images import prepare_logo


@pytest.fixture(scope="module")
def generator() -> Generator:
    return Generator()


def test_runtime_manifest_is_complete_and_versioned(generator: Generator):
    generator.check()


def test_generated_code_roundtrips_through_the_native_decoder(generator: Generator, tmp_path: Path):
    logo = prepare_logo((Path(__file__).parents[1] / "examples/alipay-logo.png").read_bytes())
    result = generator.generate("https://www.alipay.com/", logo)
    assert result.payload == "https://www.alipay.com/"
    assert result.png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(result.png) > 5000
    image = tmp_path / "code.png"
    image.write_bytes(result.png)
    assert generator.decode(image) == b"https://www.alipay.com/"


def test_decodes_the_original_user_sample(generator: Generator):
    sample = Path(__file__).parents[1] / "examples/linux-do-original.png"
    if not sample.is_file():
        pytest.skip("Original sample has been pruned from the minimal repository")
    assert generator.decode(sample) == b"https://linux.do/"


def test_overlong_url_fails_without_returning_an_image(generator: Generator):
    logo = prepare_logo((Path(__file__).parents[1] / "examples/alipay-logo.png").read_bytes())
    with pytest.raises(AppError) as error:
        generator.generate("https://example.com/" + "a" * 400, logo)
    assert error.value.code == "CAPACITY_EXCEEDED"


def test_generated_code_without_logo_roundtrips(generator: Generator, tmp_path: Path):
    result = generator.generate("https://www.alipay.com/", None)
    assert result.payload == "https://www.alipay.com/"
    image = tmp_path / "code-no-logo.png"
    image.write_bytes(result.png)
    assert generator.decode(image) == b"https://www.alipay.com/"
"""
Backend do Conversor de Arquivos.
API em Flask para conversão de imagens, áudio, vídeo e downloads do Spotify/YouTube.
"""
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from PIL import Image

app = Flask(__name__)
CORS(app)

# Formatos suportados
IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'tiff', 'tif'}
AUDIO_EXTENSIONS = {'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'wma', 'opus'}
VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv', 'm4v'}

IMAGE_FORMATS = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'tiff'}
AUDIO_FORMATS = {'mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'opus'}
VIDEO_FORMATS = {'mp4', 'avi', 'mov', 'webm', 'mkv'}

UPLOAD_FOLDER = tempfile.gettempdir()
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB


def get_extension(filename: str) -> str:
    """Retorna a extensão do arquivo em minúsculas."""
    p = Path(filename)
    return (p.suffix or "").lstrip(".").lower()


def get_file_type(ext: str) -> str:
    """Retorna o tipo do arquivo: image, audio ou video."""
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    if ext in AUDIO_EXTENSIONS:
        return 'audio'
    if ext in VIDEO_EXTENSIONS:
        return 'video'
    return 'unknown'


def convert_image(source_path: str, target_path: str, to_format: str) -> None:
    """Converte imagem usando Pillow."""
    with Image.open(source_path) as img:
        if to_format in ("jpg", "jpeg") and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        elif to_format in ("jpg", "jpeg") and img.mode == "P":
            img = img.convert("RGB")

        save_kwargs = {}
        if to_format in ("jpg", "jpeg"):
            save_kwargs["quality"] = 90
        if to_format == "webp":
            save_kwargs["quality"] = 90

        img.save(target_path, format=to_format.upper() if to_format == "tiff" else to_format, **save_kwargs)


def convert_audio_video(source_path: str, target_path: str, to_format: str, file_type: str) -> None:
    """Converte áudio ou vídeo usando ffmpeg."""
    # Verifica se ffmpeg está disponível
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise Exception("ffmpeg não encontrado. Instale o ffmpeg para converter áudio/vídeo.")

    codec_map = {
        'mp3': ('libmp3lame', 'audio'),
        'wav': ('pcm_s16le', 'audio'),
        'ogg': ('libvorbis', 'audio'),
        'flac': ('flac', 'audio'),
        'aac': ('aac', 'audio'),
        'm4a': ('aac', 'audio'),
        'opus': ('libopus', 'audio'),
        'mp4': ('libx264', 'video'),
        'webm': ('libvpx-vp9', 'video'),
        'avi': ('libx264', 'video'),
        'mov': ('libx264', 'video'),
        'mkv': ('libx264', 'video'),
    }

    codec, expected_type = codec_map.get(to_format, ('copy', file_type))

    cmd = ['ffmpeg', '-i', source_path, '-y']
    
    if file_type == 'audio' and expected_type == 'audio':
        cmd.extend(['-acodec', codec])
        cmd.extend(['-vn'])  # Remove vídeo se houver
    elif file_type == 'video' and expected_type == 'video':
        cmd.extend(['-c:v', codec])
        cmd.extend(['-c:a', 'aac'])
    elif file_type == 'video' and expected_type == 'audio':
        # Extrair áudio de vídeo
        cmd.extend(['-vn', '-acodec', codec])
    else:
        cmd.extend(['-c', 'copy'])

    cmd.append(target_path)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise Exception(f"Erro no ffmpeg: {result.stderr[:200]}")


def download_with_ytdlp(url: str, output_path: str, format_type: str = 'mp3', quality: str = 'best', allow_playlist: bool = False) -> str:
    """Baixa conteúdo do YouTube/Spotify usando yt-dlp."""
    try:
        subprocess.run(['yt-dlp', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise Exception("yt-dlp não encontrado. Instale: pip install yt-dlp")

    cmd = ['yt-dlp', '-o', output_path]
    
    # Para Spotify, o yt-dlp pode precisar de plugins extras
    # Se não funcionar, pode ser necessário instalar: pip install yt-dlp[spotify]
    # ou usar uma ferramenta dedicada como spotdl
    
    if not allow_playlist:
        cmd.append('--no-playlist')

    if format_type == 'mp3':
        cmd.extend(['-x', '--audio-format', 'mp3', '--audio-quality', '0'])
    elif format_type == 'mp4':
        if quality == 'best':
            cmd.extend(['-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'])
        else:
            # Mapear qualidade para formato do yt-dlp
            quality_map = {
                '1080p': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]',
                '720p': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]',
                '480p': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]',
                '360p': 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]',
            }
            cmd.extend(['-f', quality_map.get(quality, 'best')])

    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise Exception(f"Erro no yt-dlp: {result.stderr[:300]}")

    # yt-dlp pode adicionar extensão, então vamos procurar o arquivo gerado
    base_path = Path(output_path)
    possible_files = list(base_path.parent.glob(base_path.stem + '*'))
    if possible_files:
        # Se for playlist, pode ter múltiplos arquivos - retornar o primeiro
        return str(possible_files[0])
    return output_path


@app.route("/api/convert", methods=["POST"])
def convert():
    """Recebe um arquivo e o formato desejado; devolve o arquivo convertido."""
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400

    file = request.files["file"]
    to_format = (request.form.get("to_format") or "").strip().lower()

    if not file or file.filename == "":
        return jsonify({"error": "Arquivo não selecionado"}), 400

    if not to_format:
        return jsonify({"error": "Formato de destino não especificado"}), 400

    ext = get_extension(file.filename)
    file_type = get_file_type(ext)

    if file_type == 'unknown':
        return jsonify({"error": "Tipo de arquivo não suportado"}), 400

    # Validar formato de destino baseado no tipo
    if file_type == 'image' and to_format not in IMAGE_FORMATS:
        return jsonify({"error": "Formato de destino inválido para imagem"}), 400
    if file_type == 'audio' and to_format not in AUDIO_FORMATS:
        return jsonify({"error": "Formato de destino inválido para áudio"}), 400
    if file_type == 'video' and to_format not in VIDEO_FORMATS:
        return jsonify({"error": "Formato de destino inválido para vídeo"}), 400

    tmp_in_path = None
    tmp_out_path = None
    try:
        fd_in, tmp_in_path = tempfile.mkstemp(suffix="." + ext)
        try:
            file.save(tmp_in_path)
        finally:
            os.close(fd_in)

        base_name = Path(secure_filename(file.filename)).stem
        out_name = f"{base_name}_converted.{to_format}"
        tmp_out_path = os.path.join(UPLOAD_FOLDER, f"conv_{uuid.uuid4().hex}.{to_format}")

        if file_type == 'image':
            convert_image(tmp_in_path, tmp_out_path, to_format)
        else:
            convert_audio_video(tmp_in_path, tmp_out_path, to_format, file_type)

        # Determinar MIME type
        mime_map = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
            'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp',
            'bmp': 'image/bmp', 'tiff': 'image/tiff',
            'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg',
            'flac': 'audio/flac', 'aac': 'audio/aac', 'm4a': 'audio/mp4',
            'mp4': 'video/mp4', 'webm': 'video/webm', 'avi': 'video/x-msvideo',
            'mov': 'video/quicktime', 'mkv': 'video/x-matroska'
        }
        mime = mime_map.get(to_format, 'application/octet-stream')

        return send_file(
            tmp_out_path,
            as_attachment=True,
            download_name=out_name,
            mimetype=mime,
        )
    except Exception as e:
        return jsonify({"error": f"Erro ao converter: {str(e)}"}), 500
    finally:
        for path in (tmp_out_path, tmp_in_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


@app.route("/api/spotify", methods=["POST"])
def spotify():
    """Baixa música/playlist do Spotify em MP3."""
    data = request.get_json()
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL não fornecida"}), 400

    if 'spotify.com' not in url:
        return jsonify({"error": "URL inválida do Spotify"}), 400

    is_playlist = 'playlist' in url.lower()
    tmp_out_path = None
    try:
        # Criar arquivo temporário para o download
        tmp_out_path = os.path.join(UPLOAD_FOLDER, f"spotify_{uuid.uuid4().hex}.%(ext)s" if is_playlist else f"spotify_{uuid.uuid4().hex}.mp3")
        
        # Baixar usando yt-dlp (Spotify funciona via busca no YouTube)
        # Para playlists, permitir múltiplos arquivos
        downloaded_path = download_with_ytdlp(url, tmp_out_path, format_type='mp3', allow_playlist=is_playlist)
        
        if not os.path.exists(downloaded_path):
            return jsonify({"error": "Arquivo não foi baixado"}), 500

        # Nome do arquivo baseado na URL
        safe_name = re.sub(r'[^\w\-_\.]', '_', url.split('/')[-1].split('?')[0])
        out_name = f"spotify_{safe_name}.mp3" if safe_name else "spotify_download.mp3"

        return send_file(
            downloaded_path,
            as_attachment=True,
            download_name=out_name,
            mimetype='audio/mpeg',
        )
    except Exception as e:
        error_msg = str(e)
        if 'spotify' in error_msg.lower() or 'extractor' in error_msg.lower():
            error_msg += " Nota: Pode ser necessário instalar plugins extras do yt-dlp para Spotify."
        return jsonify({"error": f"Erro ao baixar do Spotify: {error_msg}"}), 500
    finally:
        if tmp_out_path and os.path.exists(tmp_out_path):
            try:
                os.remove(tmp_out_path)
            except OSError:
                pass


@app.route("/api/youtube", methods=["POST"])
def youtube():
    """Baixa vídeo do YouTube em MP4 ou MP3."""
    data = request.get_json()
    url = (data.get("url") or "").strip()
    format_type = (data.get("format") or "mp3").lower()
    quality = (data.get("quality") or "best").lower()

    if not url:
        return jsonify({"error": "URL não fornecida"}), 400

    if 'youtube.com' not in url and 'youtu.be' not in url:
        return jsonify({"error": "URL inválida do YouTube"}), 400

    if format_type not in ('mp3', 'mp4'):
        return jsonify({"error": "Formato deve ser mp3 ou mp4"}), 400

    tmp_out_path = None
    try:
        ext = 'mp3' if format_type == 'mp3' else 'mp4'
        tmp_out_path = os.path.join(UPLOAD_FOLDER, f"youtube_{uuid.uuid4().hex}.{ext}")
        
        downloaded_path = download_with_ytdlp(url, tmp_out_path, format_type=format_type, quality=quality)
        
        if not os.path.exists(downloaded_path):
            return jsonify({"error": "Arquivo não foi baixado"}), 500

        # Extrair título do vídeo se possível (opcional)
        out_name = f"youtube_download.{ext}"

        mime = 'audio/mpeg' if format_type == 'mp3' else 'video/mp4'
        return send_file(
            downloaded_path,
            as_attachment=True,
            download_name=out_name,
            mimetype=mime,
        )
    except Exception as e:
        return jsonify({"error": f"Erro ao baixar do YouTube: {str(e)}"}), 500
    finally:
        if tmp_out_path and os.path.exists(tmp_out_path):
            try:
                os.remove(tmp_out_path)
            except OSError:
                pass
        # Limpar outros arquivos temporários do yt-dlp na pasta
        try:
            for f in Path(UPLOAD_FOLDER).glob(f"youtube_*"):
                try:
                    if os.path.exists(f) and str(f) != str(tmp_out_path):
                        os.remove(f)
                except:
                    pass
        except:
            pass


@app.route("/api/formats", methods=["GET"])
def list_formats():
    """Lista formatos suportados."""
    return jsonify({
        "image": list(IMAGE_FORMATS),
        "audio": list(AUDIO_FORMATS),
        "video": list(VIDEO_FORMATS),
    })


@app.route("/")
def index():
    """Página inicial da API."""
    return """
    <html>
    <body style="font-family: sans-serif; padding: 2rem;">
    <h1>Conversor API</h1>
    <p>Use a interface em <code>index.html</code> ou envie requisições para:</p>
    <ul>
    <li><code>POST /api/convert</code> - Converter arquivos (imagem, áudio, vídeo)</li>
    <li><code>POST /api/spotify</code> - Baixar do Spotify (MP3)</li>
    <li><code>POST /api/youtube</code> - Baixar do YouTube (MP3 ou MP4)</li>
    </ul>
    </body>
    </html>
    """


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)

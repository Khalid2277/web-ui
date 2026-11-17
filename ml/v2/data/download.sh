#!/usr/bin/env bash
# Download the public tomato datasets used by ml/v2 into $RAW (default /workspace/agri/raw).
set -euo pipefail
RAW=${RAW:-/workspace/agri/raw}
mkdir -p "$RAW" && cd "$RAW"

# PlantVillage (lab photos): only the tomato colour folders, via sparse checkout.
[ -d plantvillage ] || git clone -q --depth 1 --filter=blob:none --sparse https://github.com/spMohanty/PlantVillage-Dataset.git plantvillage
# color = training photos; segmented = leaf on black, used to paste lab leaves onto real backgrounds.
(cd plantvillage && git sparse-checkout set --no-cone '/raw/color/Tomato___*/' '/raw/segmented/Tomato___*/')

# PlantDoc (field photos): classification folders + object-detection boxes.
[ -d plantdoc ]    || git clone -q --depth 1 https://github.com/pratikkayal/PlantDoc-Dataset.git plantdoc
[ -d plantdoc_od ] || git clone -q --depth 1 https://github.com/pratikkayal/PlantDoc-Object-Detection-Dataset.git plantdoc_od

# Tomato-Village (field photos, India).
[ -d tomato_village ] || git clone -q --depth 1 https://github.com/mamta-joshi-gehlot/Tomato-Village.git tomato_village

# PlantWild (in-the-wild photos, 1.6 GB zip): tomato classes + other plants as hard negatives.
source /workspace/agri/.venv/bin/activate
if [ ! -d plantwild/plantwild_v2 ]; then
  mkdir -p plantwild
  python -c "from huggingface_hub import hf_hub_download; hf_hub_download('uqtwei2/PlantWild', 'plantwild_v2.zip', repo_type='dataset', local_dir='plantwild')"
  (cd plantwild && unzip -q -o plantwild_v2.zip && rm plantwild_v2.zip)
fi

# Imagenette 160 px (10 everyday object classes): generic "no tomato leaf" negatives and paste backgrounds.
if [ ! -d imagenette ]; then
  curl -fsSL https://s3.amazonaws.com/fast-ai-imageclas/imagenette2-160.tgz | tar xz && mv imagenette2-160 imagenette
fi

du -sh "$RAW"/* 2>/dev/null
echo DOWNLOAD_DONE

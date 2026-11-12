from pathlib import Path
import copy
import xml.etree.ElementTree as ET
import pytest
from PIL import Image
from xhs_importer.profile import matching_nodes, unique_node

FIXTURES = Path(__file__).parent / 'fixtures' / 'xhs-9.33.4'


def snapshot(name):
    return (FIXTURES / name).read_text(encoding='utf-8')


def test_add_role_uses_favorites_header_not_duplicated_resource_id():
    node = unique_node(snapshot('favorites-panel.xml'), {'xhs': 'favorites-add'})
    assert node['bounds'] == '[264,1020][400,1156]'
    assert matching_nodes(snapshot('album-picker.xml'), {'xhs': 'favorites-add'}) == []


def test_album_image_is_only_selected_inside_the_named_single_image_album():
    selector = {'xhs': 'album-image', 'album': 'XHSImport_108bb598befa'}
    assert unique_node(snapshot('single-album.xml'), selector)['bounds'] == '[0,136][476,612]'
    assert matching_nodes(snapshot('album-picker.xml'), selector) == []
    assert matching_nodes(snapshot('single-album.xml'), dict(selector, album='XHSImport_other')) == []
    root = ET.fromstring(snapshot('single-album.xml'))
    grid = next(n for n in root.iter('node') if n.get('class') == 'androidx.recyclerview.widget.RecyclerView' and n.get('resource-id'))
    grid.append(copy.deepcopy(list(grid)[0]))
    with pytest.raises(ValueError, match='2'):
        unique_node(ET.tostring(root, encoding='unicode'), selector)


def test_album_menu_role_excludes_close_and_image_tiles():
    node = unique_node(snapshot('single-album.xml'), {'xhs': 'album-menu'})
    assert node['bounds'] == '[518,48][922,136]'
    assert matching_nodes(snapshot('preview.xml'), {'xhs': 'album-menu'}) == []


def test_add_icon_verification_rejects_blank_or_unrelated_thumbnail():
    from xhs_importer.xhs_adapter import verify_add_icon
    with Image.open(FIXTURES / 'add-icon.png') as icon:
        verify_add_icon(icon.copy(), {'bounds': '[0,0][136,136]'})
        verify_add_icon(icon.resize((204, 204)), {'bounds': '[0,0][204,204]'})
    for color in ('white', 'black', 'red'):
        with pytest.raises(ValueError, match='图标'):
            verify_add_icon(Image.new('RGB', (136,136), color), {'bounds': '[0,0][136,136]'})


def test_preset_is_version_locked_and_requires_fresh_trial():
    from xhs_importer.xhs_adapter import make_profile
    p = make_profile('9.33.4')
    p.validate()
    assert p.package == 'com.xingin.xhs'
    assert p.pick_steps[-1].action == 'target'
    assert p.album_marker == {'text': '{album}', 'package': 'com.xingin.xhs'}
    with pytest.raises(ValueError):
        p.validate(auto=True)
    with pytest.raises(ValueError, match='版本'):
        make_profile('9.34.0')
    p.version = '9.34.0'
    with pytest.raises(ValueError, match='版本'):
        p.validate()


def test_visual_recipe_cannot_be_edited_into_unverified_flow():
    from xhs_importer.xhs_adapter import make_profile
    p = make_profile('9.33.4')
    assert p.verifier == 'xhs-9.33.4-visual-v1'
    p.submit_steps[1].selector = {'text': '发送'}
    with pytest.raises(ValueError): p.validate()


def test_missing_role_error_names_the_expected_control():
    with pytest.raises(ValueError,match='相册选择下拉入口'):
        unique_node('<hierarchy/>',{'xhs':'album-menu'})

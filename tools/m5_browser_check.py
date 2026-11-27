"""Headless real browser smoke: no fabricated results; execute a genuine trial."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import sync_playwright,expect
from tools.m5_lab import Lab,Registry,Server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory() as temp:
        lab=Lab(Path(temp),Registry())
        with Server(('127.0.0.1',0),lab) as server:
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with sync_playwright() as p:
                    browser=p.chromium.launch(headless=True)
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}')
                    page.locator('#controller option').first.wait_for(state='attached')
                    page.locator('#controller').select_option('metarescue')
                    page.locator('#environment').select_option('reference')
                    page.locator('#profile').select_option('K')
                    page.locator('#confirm').check()
                    page.locator('#start').click()
                    expect(page.locator('#series-state')).to_have_text('Завершена',timeout=90000)
                    assert page.locator('#exchange-stat').inner_text()=='1/1'
                    assert int(page.locator('#truth-stat').inner_text())==2
                    page.locator('#results button').first.click()
                    expect(page.locator('#diagnostic')).to_contain_text('Физический результат: 2 / 2',timeout=10000)
                    expect(page.locator('#matrix')).to_contain_text('metarescue → reference',timeout=10000)
                    page.locator('#matrix-mode').select_option('declared')
                    expect(page.locator('#matrix')).to_contain_text('Нет испытаний по фильтру.')
                    page.locator('#matrix-mode').select_option('strict')
                    expect(page.locator('#matrix')).to_contain_text('metarescue → reference')
                    page.locator('#matrix-profile').select_option('A')
                    expect(page.locator('#matrix')).to_contain_text('Нет испытаний по фильтру.')
                    page.locator('#matrix-profile').select_option('K')
                    expect(page.locator('#matrix')).to_contain_text('metarescue → reference')
                    page.locator('#results').get_by_role('button',name='Журнал').click()
                    expect(page.locator('#timeline')).to_contain_text('decision · metarescue',timeout=10000)
                    expect(page.locator('#timeline')).to_contain_text('step_response · metarescue')
                    decision_id=page.locator('#timeline tr').filter(has_text='decision · metarescue').first.locator('td').nth(5).inner_text()
                    assert decision_id!='—'
                    page.locator('#timeline-search').fill(decision_id)
                    page.locator('#timeline-find').click()
                    expect(page.locator('#timeline-status')).to_contain_text('найдено')
                    expect(page.locator('#timeline')).to_contain_text(decision_id)
                    for row in page.locator('#timeline tr').all():
                        assert decision_id in [cell.inner_text() for cell in row.locator('td').all()[5:]]
                    page.locator('#timeline-reset').click()
                    expect(page.locator('#timeline')).to_contain_text('decision · metarescue')
                    assert not errors,errors
                    page.screenshot(path=str(args.output/'m5-desktop.png'),full_page=True)
                    page.reload();expect(page.locator('#exchange-stat')).to_have_text('1/1',timeout=10000)
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(args.output/'m5-mobile.png'),full_page=True)
                    assert page.locator('body').bounding_box()['width']<=391
                    browser.close()
            finally:
                lab.shutdown();server.shutdown();thread.join()


if __name__=='__main__':
    main()

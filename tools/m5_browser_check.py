"""Headless real browser smoke: no fabricated results; execute a genuine trial."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import sync_playwright
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
                    page.wait_for_function("document.querySelector('#series-state').textContent==='Завершена'",timeout=90000)
                    assert page.locator('#exchange-stat').inner_text()=='1/1'
                    assert int(page.locator('#truth-stat').inner_text())==2
                    page.locator('#results button').first.click()
                    page.wait_for_function("document.querySelector('#diagnostic').textContent.includes('Физический результат: 2 / 2')",timeout=10000)
                    assert not errors,errors
                    page.screenshot(path=str(args.output/'m5-desktop.png'),full_page=True)
                    page.reload();page.wait_for_function("document.querySelector('#exchange-stat').textContent==='1/1'")
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(args.output/'m5-mobile.png'),full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    browser.close()
            finally:
                lab.shutdown();server.shutdown();thread.join()


if __name__=='__main__':
    main()

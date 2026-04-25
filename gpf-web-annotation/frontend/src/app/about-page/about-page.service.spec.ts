import { TestBed } from '@angular/core/testing';

import { AboutPageService } from './about-page.service';
import { HttpClient, provideHttpClient } from '@angular/common/http';
import { lastValueFrom, of, take } from 'rxjs';
import { provideHttpClientTesting } from '@angular/common/http/testing';

describe('AboutPageService', () => {
  let service: AboutPageService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
      ]
    });
    service = TestBed.inject(AboutPageService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should get about page markdown content', async() => {
    const httpGetSpy = jest.spyOn(HttpClient.prototype, 'get');
    httpGetSpy.mockReturnValue(of('# About page content'));
    const getResponse = service.getContent();

    expect(httpGetSpy).toHaveBeenCalledWith('//localhost:8000/api/about');
    const res = await lastValueFrom(getResponse.pipe(take(1)));
    expect(res).toBe('# About page content');
  });
});

import { ComponentFixture, TestBed } from '@angular/core/testing';

import { AboutPageComponent } from './about-page.component';
import { Observable, of } from 'rxjs';
import { AboutPageService } from './about-page.service';
import { provideMarkdown } from 'ngx-markdown';
import { provideHttpClient } from '@angular/common/http';

class MockAboutPageService {
  public getContent():Observable<string> {
    return of('# Mock content');
  }
}


describe('AboutPageComponent', () => {
  let component: AboutPageComponent;
  let fixture: ComponentFixture<AboutPageComponent>;
  const mockAboutPageService = new MockAboutPageService();

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [AboutPageComponent],
      providers: [
        { provide: AboutPageService, useValue: mockAboutPageService},
        provideMarkdown(),
        provideHttpClient(),
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AboutPageComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should get page content', () => {
    expect(component.content).toBe('# Mock content');
  });
});

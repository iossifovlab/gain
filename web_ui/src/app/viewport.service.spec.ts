import { TestBed } from '@angular/core/testing';
import { ViewportService } from './viewport.service';

describe('ViewportService', () => {
  let service: ViewportService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(ViewportService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should set isMobile to true when window width is mobile on resize', () => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 800 });
    window.dispatchEvent(new Event('resize'));
    expect(service.isMobile()).toBe(true);
  });

  it('should set isMobile to false when window width is desktop on resize', () => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 });
    window.dispatchEvent(new Event('resize'));
    expect(service.isMobile()).toBe(false);
  });
});

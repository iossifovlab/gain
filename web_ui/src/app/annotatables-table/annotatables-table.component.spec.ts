import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AnnotatablesTableComponent } from './annotatables-table.component';
import { SingleAnnotationService } from '../single-annotation.service';
import { Observable, of } from 'rxjs';
import { cloneDeep } from 'lodash';


const mockAnnotatableHistory = ['chr1 11777321 G A', 'chr1 11999921 G TT'];
class SingleAnnotationServiceMock {
  public getAnnotatablesHistory(): Observable<string[]> {
    return of(cloneDeep(mockAnnotatableHistory));
  }

  public deleteAnnotatable(annotatableId: number): Observable<object> {
    mockAnnotatableHistory.splice(annotatableId, 1);
    return of({});
  }
}

describe('AnnotatablesTableComponent', () => {
  let component: AnnotatablesTableComponent;
  let fixture: ComponentFixture<AnnotatablesTableComponent>;
  const singleAnnotationServiceMock = new SingleAnnotationServiceMock();

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AnnotatablesTableComponent],
      providers: [
        { provide: SingleAnnotationService, useValue: singleAnnotationServiceMock }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(AnnotatablesTableComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should request annotatables history on init', () => {
    const getAnnotatableSpy = jest.spyOn(singleAnnotationServiceMock, 'getAnnotatablesHistory');
    component.ngOnInit();
    expect(getAnnotatableSpy).toHaveBeenCalledWith();
    expect(component.annotatables).toStrictEqual(['chr1 11777321 G A', 'chr1 11999921 G TT']);
  });

  it('should refresh history table on init', () => {
    const getAnnotatableSpy = jest.spyOn(singleAnnotationServiceMock, 'getAnnotatablesHistory');
    component.ngOnInit();
    expect(getAnnotatableSpy).toHaveBeenCalledWith();
    expect(component.annotatables).toStrictEqual(['chr1 11777321 G A', 'chr1 11999921 G TT']);
  });

  it('should delete annotatable by id from history table', () => {
    const getAnnotatableSpy = jest.spyOn(singleAnnotationServiceMock, 'getAnnotatablesHistory');
    component.onDelete(0);
    expect(getAnnotatableSpy).toHaveBeenCalledWith();
    expect(component.annotatables).toStrictEqual(['chr1 11999921 G TT']);
  });

  it('should trigger request by clicking on annotatable', () => {
    const emitSpy = jest.spyOn(component.emitAnnotatable, 'emit');
    component.makeRequest(mockAnnotatableHistory[1]);
    expect(emitSpy).toHaveBeenCalledWith(mockAnnotatableHistory[1]);
  });
});

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AnnotatablesTableComponent } from './annotatables-table.component';
import { SingleAnnotationService } from '../single-annotation.service';
import { AnnotatableHistory } from '../single-annotation';
import { Observable, of } from 'rxjs';
import { cloneDeep } from 'lodash';


const mockHistory: AnnotatableHistory[] = [
  new AnnotatableHistory(1, 'chr1 11777321 G A', ''),
  new AnnotatableHistory(2, 'chr1 11999921 G TT', 'interesting variant'),
];

class SingleAnnotationServiceMock {
  public getAnnotatablesHistory(): Observable<AnnotatableHistory[]> {
    return of(cloneDeep(mockHistory));
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public deleteAnnotatable(annotatableId: number): Observable<object> {
    return of({});
  }

  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  public updateNote(allele: string, note: string): Observable<object> {
    return of({});
  }
}

describe('AnnotatablesTableComponent', () => {
  let component: AnnotatablesTableComponent;
  let fixture: ComponentFixture<AnnotatablesTableComponent>;
  let serviceMock: SingleAnnotationServiceMock;

  beforeEach(() => {
    serviceMock = new SingleAnnotationServiceMock();
    TestBed.configureTestingModule({
      imports: [AnnotatablesTableComponent],
      providers: [
        { provide: SingleAnnotationService, useValue: serviceMock }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(AnnotatablesTableComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should request annotatables history on init', () => {
    const getAnnotatableSpy = jest.spyOn(serviceMock, 'getAnnotatablesHistory');
    component.ngOnInit();
    expect(getAnnotatableSpy).toHaveBeenCalledWith();
    expect(component.annotatables).toStrictEqual(mockHistory);
  });

  it('should refresh history table on init', () => {
    const getAnnotatableSpy = jest.spyOn(serviceMock, 'getAnnotatablesHistory');
    component.ngOnInit();
    expect(getAnnotatableSpy).toHaveBeenCalledWith();
    expect(component.annotatables).toStrictEqual(mockHistory);
  });

  it('should delete annotatable by id from history table', () => {
    const deleteSpy = jest.spyOn(serviceMock, 'deleteAnnotatable');
    component.ngOnInit();
    component.onDelete(1);
    expect(deleteSpy).toHaveBeenCalledWith(1);
  });

  it('should trigger request by clicking on annotatable', () => {
    const emitSpy = jest.spyOn(component.emitAnnotatable, 'emit');
    component.makeRequest('chr1 11777321 G A');
    expect(emitSpy).toHaveBeenCalledWith('chr1 11777321 G A');
  });

  describe('note editing', () => {
    beforeEach(() => {
      component.ngOnInit();
    });

    it('should set editingId and editingNote when startEdit is called', () => {
      component.startEdit(mockHistory[1]);
      expect(component.editingId).toBe(2);
      expect(component.editingNote).toBe('interesting variant');
    });

    it('should clear editingId and editingNote when cancelEdit is called', () => {
      component.startEdit(mockHistory[0]);
      component.cancelEdit();
      expect(component.editingId).toBeNull();
      expect(component.editingNote).toBe('');
    });

    it('should call updateNote and update the annotatable on saveEdit', () => {
      const updateSpy = jest.spyOn(serviceMock, 'updateNote');
      const annotatable = component.annotatables[0];
      component.startEdit(annotatable);
      component.editingNote = 'new label';
      component.saveEdit(annotatable);
      expect(updateSpy).toHaveBeenCalledWith('chr1 11777321 G A', 'new label');
      expect(annotatable.note).toBe('new label');
      expect(component.editingId).toBeNull();
      expect(component.editingNote).toBe('');
    });

    it('should allow saving an empty note to clear a label', () => {
      const updateSpy = jest.spyOn(serviceMock, 'updateNote');
      const annotatable = component.annotatables[1];
      component.startEdit(annotatable);
      component.editingNote = '';
      component.saveEdit(annotatable);
      expect(updateSpy).toHaveBeenCalledWith('chr1 11999921 G TT', '');
      expect(annotatable.note).toBe('');
    });

    it('should not affect other rows when editing one', () => {
      component.startEdit(mockHistory[0]);
      expect(component.editingId).toBe(1);
      expect(component.annotatables[1].note).toBe('interesting variant');
    });
  });
});

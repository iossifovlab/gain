import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AllelesTableComponent } from './alleles-table.component';
import { SingleAnnotationService } from '../single-annotation.service';
import { Observable, of } from 'rxjs';
import { cloneDeep } from 'lodash';


const mockAlleleHistory = ['chr1 11777321 G A', 'chr1 11999921 G TT'];
class SingleAnnotationServiceMock {
  public getAllelesHistory(): Observable<string[]> {
    return of(cloneDeep(mockAlleleHistory));
  }

  public deleteAllele(alleleId: number): Observable<object> {
    mockAlleleHistory.splice(alleleId, 1);
    return of({});
  }
}

describe('AllelesTableComponent', () => {
  let component: AllelesTableComponent;
  let fixture: ComponentFixture<AllelesTableComponent>;
  const singleAnnotationServiceMock = new SingleAnnotationServiceMock();

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AllelesTableComponent],
      providers: [
        { provide: SingleAnnotationService, useValue: singleAnnotationServiceMock }
      ]
    }).compileComponents();

    fixture = TestBed.createComponent(AllelesTableComponent);
    component = fixture.componentInstance;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should request alleles history on init', () => {
    const getAllelesSpy = jest.spyOn(singleAnnotationServiceMock, 'getAllelesHistory');
    component.ngOnInit();
    expect(getAllelesSpy).toHaveBeenCalledWith();
    expect(component.allelesHistory).toStrictEqual(['chr1 11777321 G A', 'chr1 11999921 G TT']);
  });

  it('should refresh history table on init', () => {
    const getAllelesSpy = jest.spyOn(singleAnnotationServiceMock, 'getAllelesHistory');
    component.ngOnInit();
    expect(getAllelesSpy).toHaveBeenCalledWith();
    expect(component.allelesHistory).toStrictEqual(['chr1 11777321 G A', 'chr1 11999921 G TT']);
  });

  it('should delete allele by id from history table', () => {
    const getAllelesSpy = jest.spyOn(singleAnnotationServiceMock, 'getAllelesHistory');
    component.onDelete(0);
    expect(getAllelesSpy).toHaveBeenCalledWith();
    expect(component.allelesHistory).toStrictEqual(['chr1 11999921 G TT']);
  });

  it('should trigger request by clicking on allele', () => {
    const emitSpy = jest.spyOn(component.emitAllele, 'emit');
    component.makeRequest(mockAlleleHistory[1]);
    expect(emitSpy).toHaveBeenCalledWith(mockAlleleHistory[1]);
  });
});

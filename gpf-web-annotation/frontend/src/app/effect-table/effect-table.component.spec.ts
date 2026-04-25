import { ComponentFixture, TestBed } from '@angular/core/testing';
import { EffectTableComponent } from './effect-table.component';
import { EffectDetail } from './effect-details';

describe('EffectDetailsComponent', () => {
  let component: EffectTableComponent;
  let fixture: ComponentFixture<EffectTableComponent>;

  beforeEach(async() => {
    await TestBed.configureTestingModule({
      imports: [EffectTableComponent]
    }).compileComponents();

    fixture = TestBed.createComponent(EffectTableComponent);
    component = fixture.componentInstance;
    component.rawEffectDetails = 'NM_001409_1:MEGF6:frame-shift:82/1541';
    component.source = 'effect_details';
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should check effect details when source is effect_details', () => {
    const expectedDetailsObject = [
      new EffectDetail(
        'MEGF6',
        'frame-shift',
        'NM_001409_1',
        '82/1541'
      )
    ];
    expect(component.effectDetails).toStrictEqual(expectedDetailsObject);
  });

  it('should check effect details when source is gene_effects', () => {
    component.rawEffectDetails = 'MTHFR:missense|MTHFR:5\'UTR';
    component.source = 'gene_effects';
    component.ngOnInit();
    const expectedDetailsObject = [
      new EffectDetail(
        'MTHFR',
        'missense',
      ),
      new EffectDetail(
        'MTHFR',
        '5\'UTR',
      ),
    ];
    expect(component.effectDetails).toStrictEqual(expectedDetailsObject);
  });

  it('should check effect details when annotator is simple effect annotator', () => {
    component.rawEffectDetails = 'ENST00000376583.7:MTHFR:coding|ENST00000376585.6:MTHFR:coding';
    component.source = 'effect_details';
    component.annotator = 'simple_effect_annotator';
    component.ngOnInit();
    const expectedDetailsObject = [
      new EffectDetail(
        'MTHFR',
        'coding',
        'ENST00000376583.7',
      ),
      new EffectDetail(
        'MTHFR',
        'coding',
        'ENST00000376585.6',
      ),
    ];
    expect(component.effectDetails).toStrictEqual(expectedDetailsObject);
  });

  it('should sort details data by gene', () => {
    component.rawEffectDetails =
    'NM_001129979_1:SYCE1L:nonsense:138/242(Trp->End)|' +
    'NM_001170629_1:CHD8:frame-shift:734/2581|' +
    'NM_001409_1:MEGF6:frame-shift:82/1541';

    component.ngOnInit();
    const expectedDetailsObject = [
      new EffectDetail(
        'CHD8',
        'frame-shift',
        'NM_001170629_1',
        '734/2581',
      ),
      new EffectDetail(
        'MEGF6',
        'frame-shift',
        'NM_001409_1',
        '82/1541',
      ),
      new EffectDetail(
        'SYCE1L',
        'nonsense',
        'NM_001129979_1',
        '138/242(Trp->End)',
      ),
    ];
    expect(component.effectDetails).toStrictEqual(expectedDetailsObject);
  });
});

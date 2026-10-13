import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Obligations from '../Obligations'

vi.mock('../../services/api')

describe('<Obligations />', () => {
  it('renders the obligations header', async () => {
    renderWithRouter(<Obligations />)
    expect(await screen.findByRole('heading', { name: /Obligations/i, level: 1 })).toBeInTheDocument()
  })
})
